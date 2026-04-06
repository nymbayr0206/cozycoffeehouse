# -*- coding: utf-8 -*-
from . import models
from . import services
from . import controllers
from . import wizard


def post_init_hook(env):
    """Keep kiosk presets limited to dine-in and takeaway options."""
    kiosk_configs = env["pos.config"].search([("self_ordering_mode", "=", "kiosk")])
    if not kiosk_configs:
        return

    self_presets = env["pos.preset"].search(
        [
            ("available_in_self", "=", True),
            ("service_at", "in", ["counter", "table"]),
        ]
    )
    if not self_presets:
        return

    takeout = self_presets.filtered(lambda preset: preset.service_at == "counter")[:1]

    for config in kiosk_configs:
        values = {}
        if set(config.available_preset_ids.ids) != set(self_presets.ids):
            values["available_preset_ids"] = [(6, 0, self_presets.ids)]
        if not config.use_presets and len(self_presets) > 1:
            values["use_presets"] = True
        if not config.default_preset_id or config.default_preset_id not in self_presets:
            values["default_preset_id"] = (takeout or self_presets[:1]).id
        if values:
            config.write(values)
