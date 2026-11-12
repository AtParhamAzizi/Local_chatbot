ALTER TABLE ingestion_jobs MODIFY COLUMN kind ENUM('json', 'pdf', 'import') NOT NULL;
ALTER TABLE pdf_chunks MODIFY COLUMN page_start INT NULL, MODIFY COLUMN page_end INT NULL;
