import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class KitchenDisplayController(http.Controller):

    @http.route('/kitchen/orders', type='json', auth='user', methods=['POST'])
    def get_orders(self, include_done=False):
        """JSON endpoint: return active kitchen orders."""
        try:
            orders = request.env['kitchen.order'].get_kitchen_orders_data(
                include_done=include_done
            )
            return {'status': 'ok', 'orders': orders}
        except Exception as e:
            _logger.exception('Kitchen display: error fetching orders')
            return {'status': 'error', 'message': str(e)}

    @http.route('/kitchen/order/<int:order_id>/status', type='json', auth='user', methods=['POST'])
    def update_status(self, order_id, status):
        """JSON endpoint: update a kitchen order's status."""
        allowed = {'in_progress', 'done', 'pending'}
        if status not in allowed:
            return {'status': 'error', 'message': 'Invalid status'}
        try:
            order = request.env['kitchen.order'].browse(order_id)
            if not order.exists():
                return {'status': 'error', 'message': 'Order not found'}
            if status == 'in_progress':
                order.action_in_progress()
            elif status == 'done':
                order.action_done()
            else:
                order.action_reset_pending()
            return {'status': 'ok', 'order_id': order_id, 'new_status': status}
        except Exception as e:
            _logger.exception('Kitchen display: error updating order %s', order_id)
            return {'status': 'error', 'message': str(e)}
