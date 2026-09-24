"""Small, model-independent CPU detection shared by startup and resource controls."""
import os

# A resource guard for the CPU inference engine, independent of this computer.
THREAD_LIMIT = 64


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def build_profile(logical=None, physical=None, available=None):
    logical = _count(logical) or _count(available) or _count(physical) or 1
    physical_known = _count(physical) is not None
    physical = min(_count(physical) or logical, logical)
    available = min(_count(available) or logical, logical)
    maximum = min(available, THREAD_LIMIT)
    # Physical cores are a conservative starting point; more threads are still selectable.
    recommended = min(physical, maximum)
    choices = {1, maximum, recommended, max(1, maximum // 2)}
    step = 2
    while step <= maximum:
        choices.add(step)
        step *= 2
    return {
        'logical_processors': logical,
        'physical_cores': physical,
        'physical_cores_detected': physical_known,
        'available_processors': available,
        'max_threads': maximum,
        'default_threads': recommended,
        'recommended_threads': recommended,
        'choices': sorted(choices),
        'thread_limit': THREAD_LIMIT,
        'recommendation_basis': 'physical_cores' if physical_known else 'available_processors',
    }


def _read(callback):
    try:
        return callback()
    except Exception:  # Platform / process permissions must never prevent startup.
        return None


def detect_profile(os_api=os, psutil_api=None):
    """Respect process CPU restrictions; tolerate unavailable platform APIs."""
    if psutil_api is None:
        try:
            import psutil as psutil_api
        except ImportError:
            psutil_api = None
    logical = _count(_read(lambda: psutil_api.cpu_count()))
    logical = logical or _count(_read(lambda: os_api.cpu_count()))
    physical = _count(_read(lambda: psutil_api.cpu_count(logical=False)))
    available = []
    process_count = _count(_read(lambda: os_api.process_cpu_count()))
    if process_count:
        available.append(process_count)
    for count in (
        _count(_read(lambda: len(set(os_api.sched_getaffinity(0))))),
        _count(_read(lambda: len(set(psutil_api.Process().cpu_affinity())))),
    ):
        if count:
            available.append(count)
    return build_profile(logical, physical, min(available) if available else None)
