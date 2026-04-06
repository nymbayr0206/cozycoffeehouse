import logging
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = 'pos.order'

    kitchen_order_id = fields.Many2one(
        'kitchen.order',
        string='Kitchen Order',
        copy=False,
        ondelete='set null',
    )

    def _get_table_number(self):
        """Safely retrieve the table number regardless of POS configuration."""
        self.ensure_one()
        # pos_restaurant module adds table_id
        if 'table_id' in self._fields and self.table_id:
            return self.table_id.name
        return ''

    def _create_or_update_kitchen_order(self):
        """Create a new kitchen.order or refresh lines on an existing one."""
        self.ensure_one()
        if not self.lines:
            return None
        # Only process if Kitchen Display is enabled for this POS config
        if self.config_id and not self.config_id.kitchen_display_enabled:
            return None

        table_number = self._get_table_number()
        order_note = ''
        if 'note' in self._fields:
            order_note = self.note or ''

        if self.kitchen_order_id:
            kitchen_order = self.kitchen_order_id
            # Only update if still active (not done)
            if kitchen_order.status != 'done':
                kitchen_order.write({
                    'table_number': table_number or '-',
                    'note': order_note,
                })
                # Rebuild lines to reflect any edits
                kitchen_order.line_ids.unlink()
                self._create_kitchen_lines(kitchen_order)
                kitchen_order._send_bus_notification('update')
        else:
            kitchen_order = self.env['kitchen.order'].create({
                'name': self.name,
                'pos_order_id': self.id,
                'table_number': table_number or '-',
                'status': 'pending',
                'note': order_note,
            })
            self._create_kitchen_lines(kitchen_order)
            # Link without re-triggering write override
            self.with_context(_kitchen_sync=True).write(
                {'kitchen_order_id': kitchen_order.id}
            )
            kitchen_order._send_bus_notification('new')

        return kitchen_order

    def _create_kitchen_lines(self, kitchen_order):
        KitchenLine = self.env['kitchen.order.line']
        for line in self.lines:
            KitchenLine.create({
                'kitchen_order_id': kitchen_order.id,
                'product_id': line.product_id.id,
                'product_name': line.product_id.display_name,
                'qty': line.qty,
                'note': (line.note or '') if 'note' in line._fields else '',
            })

    # ------------------------------------------------------------------
    # Hooks into the POS order lifecycle
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders:
            if order.lines and order.state != 'cancel':
                try:
                    order._create_or_update_kitchen_order()
                except Exception:
                    _logger.exception(
                        'Kitchen display: failed to create kitchen order for POS order %s',
                        order.name,
                    )
        return orders

    def write(self, vals):
        result = super().write(vals)
        # Skip if we are the ones setting kitchen_order_id (avoid recursion)
        if self.env.context.get('_kitchen_sync'):
            return result
        # Re-sync when lines or state change
        if 'lines' in vals or ('state' in vals and vals.get('state') != 'cancel'):
            for order in self:
                if order.lines and order.state != 'cancel':
                    try:
                        order._create_or_update_kitchen_order()
                    except Exception:
                        _logger.exception(
                            'Kitchen display: failed to update kitchen order for POS order %s',
                            order.name,
                        )
        return result

    def action_send_to_kitchen(self):
        """Manual button: (re)send this order to the kitchen display."""
        for order in self:
            order._create_or_update_kitchen_order()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': 'Sent to Kitchen',
                'message': 'Order has been sent to the kitchen display.',
                'type': 'success',
                'sticky': False,
            },
        }
