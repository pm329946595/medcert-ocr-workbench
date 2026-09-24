"""Scoped PaddleX stage timing; no changes to models, inputs or site-packages.

Access to the internal CPU pipeline is pinned to PaddleX 3.7.2. Call only while
holding the service inference lock. Restore predictors even when inference fails.
"""
from contextlib import contextmanager
from time import perf_counter


class _TimedPredictor:
    def __init__(self, predictor, stage, stats):
        self.predictor, self.stage, self.stats = predictor, stage, stats

    def __getattr__(self, name):
        return getattr(self.predictor, name)

    def __call__(self, *args, **kwargs):
        start = perf_counter()
        iterator = iter(self.predictor(*args, **kwargs))
        self.stats[self.stage] += perf_counter() - start
        while True:
            start = perf_counter()
            try:
                item = next(iterator)
            except StopIteration:
                self.stats[self.stage] += perf_counter() - start
                break
            except BaseException:
                self.stats[self.stage] += perf_counter() - start
                raise
            self.stats[self.stage] += perf_counter() - start
            yield item  # Do not count the caller's work between yields.


@contextmanager
def time_stages(engine):
    pipeline = engine.paddlex_pipeline._pipeline
    original = {name: getattr(pipeline, name) for name in ('text_det_model', 'text_rec_model')}
    stats = {'detection_seconds': 0.0, 'recognition_seconds': 0.0}
    try:
        for name, stage in zip(original, stats):
            setattr(pipeline, name, _TimedPredictor(original[name], stage, stats))
        yield stats
    finally:
        for name, predictor in original.items():
            setattr(pipeline, name, predictor)
