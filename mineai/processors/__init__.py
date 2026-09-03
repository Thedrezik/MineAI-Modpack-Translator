from importlib import import_module

__all__ = [
    "ModpackAnalyzer",
    "JarProcessor",
    "LooseJsonProcessor",
    "SnbtProcessor",
    "StringEstimator",
]

_EXPORTS = {
    "ModpackAnalyzer": ("mineai.processors.analyzer", "ModpackAnalyzer"),
    "JarProcessor": ("mineai.processors.jar", "JarProcessor"),
    "LooseJsonProcessor": ("mineai.processors.loose_json", "LooseJsonProcessor"),
    "SnbtProcessor": ("mineai.processors.snbt", "SnbtProcessor"),
    "StringEstimator": ("mineai.processors.estimator", "StringEstimator"),
}


def __getattr__(name: str):
    try:
        module_name, attribute = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name), attribute)
    globals()[name] = value
    return value
