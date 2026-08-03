from importlib import import_module

__all__ = ["TranslationJob", "JobState"]

_EXPORTS = {
    "TranslationJob": ("mineai.runtime.job", "TranslationJob"),
    "JobState": ("mineai.runtime.state", "JobState"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
