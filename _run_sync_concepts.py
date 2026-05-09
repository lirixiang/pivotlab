import sys; sys.path.insert(0, '/app/backend')
import logging; logging.basicConfig(level=logging.INFO)
from app.services.sync_service import sync_concepts
result = sync_concepts()
print(f'Done, task_id={result}')
