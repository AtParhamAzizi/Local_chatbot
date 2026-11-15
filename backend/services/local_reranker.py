"""Bounded optional local cross-encoder with immediate first-stage fallback."""

import logging
import math
import threading
from dataclasses import dataclass
from pathlib import Path

from persian_text import normalize_persian
from services.source_fusion import lexical_text


POLICY_VERSION = 'qwen-local-reranker-v1'
CANDIDATE_LIMIT = 6
BATCH_SIZE = 3
MAX_LENGTH = 512
QUERY_TOKENS = 128
MODEL_WEIGHT = 0.75
RANK_CONSTANT = 60
TASK = 'Given a web search query, retrieve relevant passages that answer the query'
PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. '
          'Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'


@dataclass(frozen=True)
class RerankerConfig:
    model_path: str
    device: str = 'cuda'
    candidates: int = CANDIDATE_LIMIT
    batch_size: int = BATCH_SIZE
    max_length: int = MAX_LENGTH
    model_weight: float = MODEL_WEIGHT

    def __post_init__(self):
        if (self.device not in {'cpu', 'cuda'} or not 1 <= self.candidates <= 24
                or not 1 <= self.batch_size <= 6 or not 256 <= self.max_length <= 512
                or not 0 <= self.model_weight <= 1):
            raise ValueError('Invalid reranker resource limits')


class QwenRerankerRuntime:
    """Loads trusted local safetensors only; no generation or embedding mutation."""

    def __init__(self, config):
        import torch
        import transformers
        from embedding_profile import local_artifact_revision, require_unchanged_artifacts

        path = Path(config.model_path)
        if not (path / 'config.json').is_file() or not (path / 'model.safetensors').is_file():
            raise ValueError('Reranker requires a complete local snapshot')
        revision, manifest = local_artifact_revision(path)
        if config.device == 'cuda' and not torch.cuda.is_available():
            raise ValueError('Selected reranker profile requires CUDA')
        self.config = config
        self.torch = torch
        self.tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(path), padding_side='left', local_files_only=True, trust_remote_code=False)
        # Pascal has limited native FP16 throughput. Keep storage packed and compute in FP32.
        dtype = torch.float32
        options = {}
        if config.device == 'cuda':
            options = {'quantization_config': transformers.BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type='nf4', bnb_4bit_compute_dtype=torch.float32,
                bnb_4bit_use_double_quant=True), 'device_map': {'': 'cuda:0'}}
        self.model = transformers.AutoModelForCausalLM.from_pretrained(
            str(path), dtype=dtype, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, attn_implementation='sdpa', **options).eval()
        if config.device == 'cpu':
            self.model = self.model.to('cpu')
        if self.model.config.model_type != 'qwen3':
            raise ValueError('Unsupported local reranker architecture')
        require_unchanged_artifacts(manifest)
        self.manifest = manifest
        self.prefix = self.encode(PREFIX + '<Instruct>: ' + TASK + '\n<Query>: ')
        self.separator = self.encode('\n<Document>: ')
        self.suffix = self.encode(SUFFIX)
        self.no_id, self.yes_id = (self.tokenizer.convert_tokens_to_ids(value) for value in ('no', 'yes'))
        if self.no_id == self.yes_id or any(value is None for value in (self.no_id, self.yes_id)):
            raise ValueError('Reranker decision tokens are unavailable')
        self.identity = {'model': 'Qwen/Qwen3-Reranker-0.6B', 'revision': revision, 'device': config.device,
                         'dtype': str(dtype), 'torch': torch.__version__, 'transformers': transformers.__version__,
                         'policy': POLICY_VERSION, 'max_length': config.max_length,
                         'batch_size': config.batch_size, 'query_tokens': QUERY_TOKENS,
                         'padding': 'max_length-left',
                         'attention': 'sdpa',
                         'quantization': 'NF4-double-quant-fp32' if config.device == 'cuda' else 'none'}

    def encode(self, text, limit=None):
        options = {} if limit is None else {'truncation': True, 'max_length': limit}
        return self.tokenizer.encode(text, add_special_tokens=False, **options)

    def inputs(self, query, documents):
        query_ids = self.encode(normalize_persian(query)[:4096], QUERY_TOKENS)
        start = self.prefix + query_ids + self.separator
        budget = self.config.max_length - len(start) - len(self.suffix)
        if budget < 1:
            raise ValueError('Reranker prompt exceeds its token budget')
        sequences = [start + self.encode(normalize_persian(text[:20000]), budget) + self.suffix for text in documents]
        return self.tokenizer.pad({'input_ids': sequences}, padding='max_length', max_length=self.config.max_length,
                                  return_tensors='pt').to(self.config.device)

    def score(self, query, documents):
        from embedding_profile import require_unchanged_artifacts

        if len(documents) > self.config.candidates:
            raise ValueError('Reranker candidate budget exceeded')
        require_unchanged_artifacts(self.manifest)
        scores = []
        with self.torch.inference_mode():
            for start in range(0, len(documents), self.config.batch_size):
                inputs = self.inputs(query, documents[start:start + self.config.batch_size])
                # The decision uses only the final position, avoiding a sequence x vocabulary tensor.
                logits = self.model(**inputs, use_cache=False, logits_to_keep=1).logits[:, -1, :]
                decisions = logits[:, [self.no_id, self.yes_id]].float().softmax(dim=-1)[:, 1]
                scores.extend(decisions.cpu().tolist())
        return scores


def order_candidates(hits, scores, *, weight=MODEL_WEIGHT, rank_constant=RANK_CONSTANT):
    if len(scores) != len(hits) or any(not math.isfinite(score) or not 0 <= score <= 1 for score in scores):
        raise ValueError('Invalid reranker scores')
    model_order = sorted(range(len(hits)), key=lambda index: -scores[index])
    model_ranks = {index: rank for rank, index in enumerate(model_order, 1)}
    ranked = [{**hit, 'rerank_score': weight / (rank_constant + model_ranks[index])
                                    + (1 - weight) / (rank_constant + index + 1)}
              for index, hit in enumerate(hits)]
    # Stable ties retain the source-independent first-stage ordering.
    ranked.sort(key=lambda hit: -hit['rerank_score'])
    return ranked


class LocalReranker:
    def __init__(self, config, *, loader=QwenRerankerRuntime):
        self.config = config
        self.loader = loader
        self.runtime = None
        self.failed = False
        self.lock = threading.Lock()

    def rerank(self, query, hits, *, limit=6):
        if limit <= 0:
            return []
        candidates = hits[:self.config.candidates]
        fallback = candidates[:limit]
        if len(candidates) < 2 or self.failed or not self.lock.acquire(blocking=False):
            return fallback
        try:
            if self.runtime is None:
                self.runtime = self.loader(self.config)
            scores = self.runtime.score(query, [lexical_text(hit) for hit in candidates])
            return order_candidates(candidates, scores, weight=self.config.model_weight)[:limit]
        except Exception:
            # One fixed diagnostic, no query, document, filesystem path or model error text.
            logging.getLogger('chatbot.reranker').warning('Local reranker unavailable; using first-stage ranking')
            self.failed = True  # no repeated cold-load/OOM attempts until restart
            self.runtime = None
            return fallback
        finally:
            self.lock.release()


class RerankingRetrievalService:
    def __init__(self, retrieval, reranker):
        self.retrieval = retrieval
        self.reranker = reranker

    def retrieve(self, query, limit=6):
        if limit <= 0:
            return []
        count = self.reranker.config.candidates
        hits = self.retrieval.retrieve(query, limit=max(limit, count))
        result = self.reranker.rerank(query, hits, limit=min(limit, count))
        return result + hits[count:limit]


_shared_reranker = None
_shared_lock = threading.Lock()


def optional_reranking(retrieval):
    from settings import setting_value

    if setting_value('RERANKER_PROFILE') == 'off':
        return retrieval
    if setting_value('RETRIEVAL_STRATEGY') != 'fusion':
        return retrieval
    # One lazy runtime across application factories, bounded to one inference at a time.
    global _shared_reranker
    with _shared_lock:
        config = RerankerConfig(setting_value('RERANKER_MODEL_PATH'))
        if _shared_reranker is None:
            _shared_reranker = LocalReranker(config)
        elif _shared_reranker.config != config:
            raise ValueError('Restart the process after changing reranker configuration')
    return RerankingRetrievalService(retrieval, _shared_reranker)
