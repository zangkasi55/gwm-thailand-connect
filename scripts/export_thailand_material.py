"""Export reviewed app provisioning data without loading Home Assistant."""

from __future__ import annotations

import argparse
import importlib.machinery
import os
import sys
import types
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    for name, path in (
        ("custom_components", root / "custom_components"),
        ("custom_components.gwm_thailand_connect", root / "custom_components" / "gwm_thailand_connect"),
        ("custom_components.gwm_thailand_connect.gwm_client", root / "custom_components" / "gwm_thailand_connect" / "gwm_client"),
    ):
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        module.__package__ = name
        module.__spec__ = importlib.machinery.ModuleSpec(name, loader=None, is_package=True)
        sys.modules[name] = module
        parent, _, child = name.rpartition(".")
        if parent:
            setattr(sys.modules[parent], child, module)
    from custom_components.gwm_thailand_connect.gwm_client.thailand_protocol import (
        load_thailand_app_material_from_apk,
        serialize_thailand_app_material,
    )

    document = serialize_thailand_app_material(load_thailand_app_material_from_apk(arguments.apk))
    descriptor = os.open(arguments.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(document)
        output.flush()
        os.fsync(output.fileno())
    print("Private provisioning file created. Keep it private and import it only into your Home Assistant.")


if __name__ == "__main__":
    main()
