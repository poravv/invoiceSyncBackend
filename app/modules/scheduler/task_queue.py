from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Dict, Optional, Any

from app.modules.scheduler.processing_lock import PROCESSING_LOCK


class TaskQueue:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._queue: list[str] = []
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._running = True
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    def enqueue(self, action: str, func: Callable[[], Any]) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                'job_id': job_id,
                'action': action,
                'status': 'queued',
                'created_at': time.time(),
                'started_at': None,
                'finished_at': None,
                'message': None,
                'result': None,
                '_func': func,
            }
            self._queue.append(job_id)
            self._cv.notify()
        return job_id

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            # do not expose internal func
            job_copy = {k: v for k, v in job.items() if k != '_func'}
            # pydantic model safe dump
            res = job_copy.get('result')
            if res is not None:
                try:
                    job_copy['result'] = res.model_dump()  # pydantic v2
                except Exception:
                    try:
                        job_copy['result'] = res.dict()  # v1 fallback
                    except Exception:
                        job_copy['result'] = res
            return job_copy

    def _loop(self):
        while self._running:
            with self._lock:
                while not self._queue and self._running:
                    self._cv.wait(timeout=1.0)
                if not self._running:
                    break
                job_id = self._queue.pop(0) if self._queue else None
                if not job_id:
                    continue
                job = self._jobs.get(job_id)
                if not job:
                    continue
                job['status'] = 'running'
                job['started_at'] = time.time()
                func = job.get('_func')
            # run outside lock but serialized with PROCESSING_LOCK
            try:
                print(f"[TaskQueue] Intentando adquirir PROCESSING_LOCK para job {job_id}")
                # Intentar adquirir el lock con timeout de 30 segundos
                lock_acquired = PROCESSING_LOCK.acquire(timeout=30)
                print(f"[TaskQueue] Lock adquirido: {lock_acquired} para job {job_id}")
                
                if not lock_acquired:
                    # Si no se pudo adquirir el lock, marcar como error
                    print(f"[TaskQueue] Timeout del lock para job {job_id}")
                    with self._lock:
                        job['status'] = 'error'
                        job['message'] = 'No se pudo adquirir el lock de procesamiento (timeout de 30 segundos). Otro proceso puede estar ejecutándose.'
                        job['finished_at'] = time.time()
                    continue
                
                try:
                    print(f"[TaskQueue] Ejecutando función para job {job_id}")
                    result = func() if callable(func) else None
                    print(f"[TaskQueue] Función completada para job {job_id}")
                    with self._lock:
                        job['result'] = result
                        job['status'] = 'done'
                        job['message'] = getattr(result, 'message', 'Completado') if result else 'Completado'
                        job['finished_at'] = time.time()
                finally:
                    print(f"[TaskQueue] Liberando lock para job {job_id}")
                    PROCESSING_LOCK.release()
                    
            except Exception as e:
                print(f"[TaskQueue] Error en job {job_id}: {e}")
                with self._lock:
                    job['status'] = 'error'
                    job['message'] = str(e)
                    job['finished_at'] = time.time()


# Global singleton
task_queue = TaskQueue()

