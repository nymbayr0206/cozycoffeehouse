/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { Component } from "@odoo/owl";
import { Dialog } from "@web/core/dialog/dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { PaymentInterface } from "@point_of_sale/app/utils/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/services/pos_store";

const POLL_DELAY_MS = 3000;

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

export class QPayPaymentPopup extends Component {
    static template = "payment_qpay_custom.QPayPaymentPopup";
    static components = { Dialog };
    static props = {
        close: Function,
        title: { type: String, optional: true },
        amount: { type: String, optional: true },
        qrCode: { type: String, optional: true },
        shortUrl: { type: String, optional: true },
        onCancel: { type: Function, optional: true },
    };
    static defaultProps = {
        title: _t("QPay Payment"),
    };

    setup() {
        this.env.dialogData.dismiss = () => this.cancel();
    }

    async cancel() {
        if (this.props.onCancel) {
            const shouldClose = await this.props.onCancel();
            if (shouldClose === false) {
                return;
            }
        }
        this.props.close();
    }
}

export class PaymentQPay extends PaymentInterface {
    setup() {
        super.setup(...arguments);
        this.pendingRequests = new Map();
    }

    get fastPayments() {
        return false;
    }

    _getPaymentLine(uuid) {
        const order = this.pos.getOrder();
        return order ? order.payment_ids.find((line) => line.uuid === uuid) : null;
    }

    _showError(message, title = _t("QPay Error")) {
        this.env.services.dialog.add(AlertDialog, {
            title,
            body: message,
        });
    }

    async _cancelTransaction(transactionId) {
        if (!transactionId) {
            return true;
        }
        try {
            await this.pos.data.silentCall("pos.payment.method", "qpay_cancel_invoice", [
                [this.payment_method_id.id],
                { transaction_id: transactionId },
            ]);
        } catch (error) {
            this._showError(
                (error && error.data && error.data.message) ||
                    _t("Failed to cancel the QPay request. Please retry from the POS."),
            );
            return false;
        }
        return true;
    }

    async _pollTransaction(pending) {
        while (!pending.cancelled) {
            await delay(POLL_DELAY_MS);
            if (pending.cancelled) {
                return { status: "cancelled" };
            }

            let response;
            try {
                response = await this.pos.data.silentCall("pos.payment.method", "qpay_check_payment", [
                    [this.payment_method_id.id],
                    { transaction_id: pending.transactionId },
                ]);
            } catch (error) {
                return {
                    status: "error",
                    message:
                        (error && error.data && error.data.message) ||
                        _t("Could not contact the server while polling QPay payment status."),
                };
            }

            if (response && response.status === "paid") {
                return response;
            }
            if (response && response.status === "error") {
                return response;
            }
        }
        return { status: "cancelled" };
    }

    _buildReceiptInfo(response, fallbackInvoiceId) {
        const reference = (response && response.qpay_payment_id) || fallbackInvoiceId;
        return reference ? _t("QPay reference: %s", reference) : "";
    }

    async sendPaymentRequest(uuid) {
        super.sendPaymentRequest(uuid);

        const line = this._getPaymentLine(uuid);
        const order = this.pos.getOrder();
        if (!line || !order) {
            return false;
        }

        if (line.amount <= 0) {
            this._showError(_t("The QPay amount must be greater than zero."));
            return false;
        }

        let response;
        try {
            response = await this.pos.data.call("pos.payment.method", "qpay_create_invoice", [
                [this.payment_method_id.id],
                {
                    amount: line.amount,
                    order_ref: order.pos_reference || order.name || order.uuid,
                    order_uuid: order.uuid,
                    partner_id: (order.partner_id && order.partner_id.id) || false,
                },
            ]);
        } catch (error) {
            this._showError(
                (error && error.data && error.data.message) ||
                    _t("Failed to create a QPay invoice from the POS."),
            );
            return false;
        }

        if (response && response.error) {
            this._showError(response.error);
            return false;
        }

        const transactionId = response && response.transaction_id;
        if (!transactionId) {
            this._showError(_t("QPay did not return a transaction id."));
            return false;
        }

        line.setPaymentStatus("waitingCard");
        line.qpay_transaction_id = transactionId;
        line.transaction_id = response.qpay_invoice_id || "";

        const pending = {
            cancelled: false,
            transactionId,
            closePopup: null,
            cancel: null,
        };
        pending.cancelPromise = new Promise((resolve) => {
            pending.cancel = async () => {
                if (pending.cancelled) {
                    resolve({ status: "cancelled" });
                    return true;
                }
                pending.cancelled = true;
                await this._cancelTransaction(transactionId);
                resolve({ status: "cancelled" });
                return true;
            };
        });

        pending.closePopup = this.env.services.dialog.add(QPayPaymentPopup, {
            title: this.payment_method_id.name || _t("QPay Payment"),
            amount: this.env.utils.formatCurrency(line.amount),
            qrCode: response.qr_image ? `data:image/png;base64,${response.qr_image}` : "",
            shortUrl: response.qpay_short_url || "",
            onCancel: pending.cancel,
        });

        this.pendingRequests.set(uuid, pending);

        try {
            const result = await Promise.race([
                this._pollTransaction(pending),
                pending.cancelPromise,
            ]);

            if (result && result.status === "paid") {
                line.transaction_id =
                    result.qpay_payment_id || response.qpay_invoice_id || "";
                const receiptInfo = this._buildReceiptInfo(result, response.qpay_invoice_id);
                if (receiptInfo) {
                    line.setReceiptInfo(receiptInfo);
                }
                line.card_type = "QPay";
                return true;
            }

            if (result && result.status === "error") {
                this._showError(result.message || _t("The QPay payment failed."));
            }
            return false;
        } finally {
            pending.cancelled = true;
            if (pending.closePopup) {
                pending.closePopup();
            }
            this.pendingRequests.delete(uuid);
        }
    }

    async sendPaymentCancel(order, uuid) {
        super.sendPaymentCancel(order, uuid);

        const pending = this.pendingRequests.get(uuid);
        if (pending && pending.cancel) {
            await pending.cancel();
            return true;
        }

        const line = this._getPaymentLine(uuid);
        if (line && line.qpay_transaction_id) {
            return this._cancelTransaction(line.qpay_transaction_id);
        }
        return true;
    }

    close() {
        for (const pending of this.pendingRequests.values()) {
            pending.cancelled = true;
            if (pending.closePopup) {
                pending.closePopup();
            }
        }
        this.pendingRequests.clear();
    }
}

register_payment_method("qpay", PaymentQPay);
