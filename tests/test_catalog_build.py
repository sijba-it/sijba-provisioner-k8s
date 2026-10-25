import json

import pytest

from catalog import CatalogError, build_catalog

ENTRY = {"type": "pushover", "label": "Pushover", "image": "ghcr.io/sijba-it/sijba-out-pushover",
         "port": 9101, "fields": [{"name": "PUSHOVER_APP_TOKEN", "secret": True}]}


@pytest.fixture
def cdir(tmp_path):
    (tmp_path / "pushover.json").write_text(json.dumps(ENTRY))
    return tmp_path


def test_builtin_entry_defaults_to_latest(cdir):
    cat = build_catalog(cdir, {})
    assert list(cat) == ["pushover"]
    assert cat["pushover"]["image"] == "ghcr.io/sijba-it/sijba-out-pushover:latest"
    assert "type" not in cat["pushover"]


def test_image_pin_from_env(cdir):
    cat = build_catalog(cdir, {"CONNECTOR_IMAGE_PUSHOVER": "ghcr.io/sijba-it/sijba-out-pushover:202610251200"})
    assert cat["pushover"]["image"].endswith(":202610251200")


def test_registry_port_is_not_a_tag(cdir):
    cat = build_catalog(cdir, {"CONNECTOR_IMAGE_PUSHOVER": "registry.local:5000/pushover"})
    assert cat["pushover"]["image"] == "registry.local:5000/pushover:latest"


def test_catalog_extra_adds_and_overrides(cdir):
    extra = {"my-thing": {"label": "Mine", "image": "me/thing:1", "port": 9000, "fields": []},
             "pushover": {**{k: v for k, v in ENTRY.items() if k != "type"}, "label": "Custom"}}
    cat = build_catalog(cdir, {"CATALOG_EXTRA": json.dumps(extra), "CONNECTOR_IMAGE_MY_THING": "me/thing:2"})
    assert cat["pushover"]["label"] == "Custom"
    assert cat["my-thing"]["image"] == "me/thing:2"


def test_bad_extra_is_rejected(cdir):
    with pytest.raises(CatalogError):
        build_catalog(cdir, {"CATALOG_EXTRA": "{nope"})
