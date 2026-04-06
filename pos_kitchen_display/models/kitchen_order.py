from odoo import models, fields, api


class KitchenOrder(models.Model):
    _name = 'kitchen.order'
    _description = 'Kitchen Display Order'
    _order = 'create_date asc'
    _rec_name = 'name'

    name = fields.Char(string='Order Reference', required=True, copy=False)
    pos_order_id = fields.Many2one(
        'pos.order', string='POS Order', ondelete='cascade', index=True
    )
    table_number = fields.Char(string='Table', default='-')
    status = fields.Selection(
        selection=[
            ('pending', 'Pending'),
            ('in_progress', 'In Progress'),
            ('done', 'Done'),
        ],
        string='Status',
        default='pending',
        required=True,
        index=True,
    )
    line_ids = fields.One2many(
        'kitchen.order.line', 'kitchen_order_id', string='Order Lines'
    )
    note = fields.Text(string='Note')
    start_time = fields.Datetime(string='Started At', readonly=True)
    done_time = fields.Datetime(string='Done At', readonly=True)
    elapsed_minutes = fields.Integer(
        string='Elapsed (min)', compute='_compute_elapsed', store=False
    )

    @api.depends('create_date', 'status')
    def _compute_elapsed(self):
        now = fields.Datetime.now()
        for order in self:
            if order.create_date:
                delta = now - order.create_date
                order.elapsed_minutes = int(delta.total_seconds() / 60)
            else:
                order.elapsed_minutes = 0

    def action_in_progress(self):
        for order in self:
            order.write({
                'status': 'in_progress',
                'start_time': fields.Datetime.now(),
            })
            order._send_bus_notification('update')
        return True

    def action_done(self):
        for order in self:
            order.write({
                'status': 'done',
                'done_time': fields.Datetime.now(),
            })
            order._send_bus_notification('update')
        return True

    def action_reset_pending(self):
        for order in self:
            order.write({
                'status': 'pending',
                'start_time': False,
                'done_time': False,
            })
            order._send_bus_notification('update')
        return True

    def _send_bus_notification(self, action):
        """Notify all kitchen display clients via WebSocket."""
        self.env['bus.bus']._sendone(
            'kitchen_display',
            'kitchen_order_update',
            {
                'action': action,
                'order_id': self.id,
                'status': self.status,
                'name': self.name,
            },
        )

    @api.model
    def get_kitchen_orders_data(self, include_done=False):
        """Return active kitchen orders as a list of dicts for the frontend."""
        domain = [('status', 'in', ['pending', 'in_progress'])]
        if include_done:
            domain = [('status', 'in', ['pending', 'in_progress', 'done'])]
        orders = self.search(domain, order='create_date asc')
        return orders._format_for_display()

    def _format_for_display(self):
        result = []
        for order in self:
            result.append({
                'id': order.id,
                'name': order.name,
                'table_number': order.table_number or '-',
                'status': order.status,
                'create_date': (
                    fields.Datetime.to_string(order.create_date)
                    if order.create_date
                    else ''
                ),
                'start_time': (
                    fields.Datetime.to_string(order.start_time)
                    if order.start_time
                    else ''
                ),
                'elapsed_minutes': order.elapsed_minutes,
                'note': order.note or '',
                'lines': [
                    {
                        'id': line.id,
                        'product_name': line.product_name,
                        'qty': line.qty,
                        'note': line.note or '',
                    }
                    for line in order.line_ids
                ],
            })
        return result


class KitchenOrderLine(models.Model):
    _name = 'kitchen.order.line'
    _description = 'Kitchen Order Line'
    _order = 'id asc'

    kitchen_order_id = fields.Many2one(
        'kitchen.order', string='Kitchen Order', ondelete='cascade', required=True
    )
    product_id = fields.Many2one('product.product', string='Product')
    product_name = fields.Char(string='Product Name', required=True)
    qty = fields.Float(string='Quantity', default=1.0)
    note = fields.Char(string='Note')
