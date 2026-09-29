import os
import threading
import torch
from transformers import (
    AutoTokenizer,
    AutoModel,
    AutoModelForCausalLM,
)
from searchForAnswer import build_context

Loggerlevel = 'generator'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GENERATOR_MODEL = os.getenv(
    'GEN_MODEL',
    os.path.join(os.path.dirname(BASE_DIR), 'backend', 'modelgen'),
)
# GENERATOR_MODEL    = os.getenv('GEN_MODEL', 'modelgen')
MAX_NEW_TOKENS     = 512
TEMPERATURE        = 0.3
TOP_P              = 0.8
TOP_K              = 15
REPETITION_PENALTY = 1.05



_gen_tokenizer = None
_gen_model     = None
_gen_lock      = threading.Lock()

SYSTEM_PROMPT = (
    "شما دستیار هوشمند سامانه پیام ایران هستید. "
    "پاسخ خود را فقط بر اساس «سیاق» و بازنویسی شده ارائه‌شده بدهید. "
    " در صورتی که موضوع سؤال نیاز به پشتیبانی یا پیگیری مستقیم دارد، کاربر را به شماره 1649 ارجاع بده."
)

# def _load_generator():

def _load_generator():
    global _gen_tokenizer, _gen_model, _gen_device

    if _gen_model is not None:
        return

    with _gen_lock:
        if _gen_model is not None:
            return

        if not os.path.isdir(GENERATOR_MODEL):
            raise FileNotFoundError(
                f'GENERATOR_MODEL folder not found: {GENERATOR_MODEL}\n'
                f'Download it with:\n'
                f'  hf download Qwen/Qwen3-1.7B --local-dir "{GENERATOR_MODEL}"'
            )
        if not os.path.isfile(os.path.join(GENERATOR_MODEL, 'config.json')):
            raise FileNotFoundError(
                f'{GENERATOR_MODEL} exists but has no config.json — '
                f'the download is incomplete.'
            )

        if torch.cuda.is_available():
            _gen_device = 'cuda'
            _gen_dtype  = torch.bfloat16
        else:
            _gen_device = 'cpu'
            _gen_dtype  = torch.float32

        print(f'[gen] loading {GENERATOR_MODEL} on {_gen_device} ({_gen_dtype}) ...')

        _gen_tokenizer = AutoTokenizer.from_pretrained(
            GENERATOR_MODEL,
            trust_remote_code=True,
            local_files_only=True,          # ← never hit the network
        )

        _gen_model = AutoModelForCausalLM.from_pretrained(
            GENERATOR_MODEL,
            trust_remote_code=True,
            torch_dtype=_gen_dtype,
            local_files_only=True,          # ← never hit the network
        ).to(_gen_device).eval()

        print(f'[gen] ready on {_gen_device}')





def get_generator():
    _load_generator()
    return _gen_tokenizer, _gen_model, _gen_device


@torch.no_grad()
def generate_reply(question: str, context_answers: list[str]) -> str:
    # tok, model = get_generator()
    tok, model, device = get_generator()


    # context = "\n---\n".join(a.strip() for a in context_answers if a.strip())
    context = build_context(context_answers)
    user_msg = user_msg = f"""
        اطلاعات مرجع بازیابی شده از پایگاه دانش:
        ----------------
        {context}
        ----------------

        سؤال کاربر:
        {question}

        با استفاده از پاسخ های بالا، یک پاسخ جدید و بازنویسی‌شده برای سؤال کاربر بنویس.
        پاسخ را عیناً از اطلاعات مرجع کپی نکن.

        پاسخ نهایی:
        """

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_msg},
    ]

    text = tok.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    # inputs = tok(text, return_tensors='pt').to(model.device)
    inputs = tok(text, return_tensors='pt').to(device)

    out = model.generate(
        **inputs,
        max_new_tokens=MAX_NEW_TOKENS,
        do_sample=True,
        temperature=TEMPERATURE,
        top_p=TOP_P,
        top_k=TOP_K,
        repetition_penalty=REPETITION_PENALTY,
        pad_token_id=tok.eos_token_id,
    )
    generated = out[0][inputs['input_ids'].shape[1]:]
    reply = tok.decode(generated, skip_special_tokens=True).strip()

    # Safety net: strip any leftover think block
    # if '' in reply:
    #     reply = reply.split('', 1)[1].strip()
    # return reply    
    if "<think>" in reply and "</think>" in reply:
        reply = reply.split("</think>", 1)[1].strip()
    return reply

