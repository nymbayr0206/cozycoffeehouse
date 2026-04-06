# -*- coding: utf-8 -*-
from . import models
from . import services
from . import controllers
from . import wizard


def post_init_hook(env):
    """
    Kiosk POS config-уудад preset-үүдийг тохируулна:
    - available_in_self = True байгаа preset-үүдийг kiosk config-т нэмнэ
    - use_presets = True болгоно
    """
    kiosk_configs = env['pos.config'].search([('self_ordering_mode', '=', 'kiosk')])
    if not kiosk_configs:
        return

    # available_in_self = True байгаа бүх preset-үүдийг авна
    self_presets = env['pos.preset'].search([('available_in_self', '=', True)])
    if not self_presets:
        return

    for config in kiosk_configs:
        # Байхгүй preset-үүдийг нэмнэ
        missing = self_presets - config.available_preset_ids
        if missing:
            config.write({
                'available_preset_ids': [(4, p.id) for p in missing],
            })
        # use_presets идэвхжүүлнэ (2+ preset байгаа тохиолдолд)
        if not config.use_presets and len(self_presets) > 1:
            config.write({'use_presets': True})
        # default_preset_id тавьгүй байвал counter/takeout preset-ийг өгнө
        if not config.default_preset_id and self_presets:
            # 'counter' service_at-тай preset-ийг default болгоно (авч явах)
            takeout = self_presets.filtered(lambda p: p.service_at == 'counter')
            config.write({'default_preset_id': (takeout or self_presets)[0].id})
