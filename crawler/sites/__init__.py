"""サイトアダプタのレジストリ。

このパッケージ内のモジュールを自動でimportし、@register の付いた
アダプタクラスを収集する。新しいサイトの追加は、モジュールを1つ置いて
config/sites.json にエントリを足すだけでよい。
"""
import importlib
import pkgutil

from crawler.sites.base import SiteAdapter

ADAPTER_CLASSES: dict[str, type[SiteAdapter]] = {}


def register(cls: type[SiteAdapter]) -> type[SiteAdapter]:
    ADAPTER_CLASSES[cls.key] = cls
    return cls


def _discover() -> None:
    for info in pkgutil.iter_modules(__path__):
        if info.name != "base":
            importlib.import_module(f"{__name__}.{info.name}")


_discover()
