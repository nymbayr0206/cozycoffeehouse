/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { onWillUnmount } from "@odoo/owl";
import { rpc } from "@web/core/network/rpc";
import { PaymentPage } from "@pos_self_order/app/pages/payment_page/payment_page";
import { SelfOrder } from "@pos_self_order/app/services/self_order_service";

const POLL_DELAY_MS = 3000;

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

patch(SelfOrder.prototype, {
    filterPaymentMethods(paymentMethods) {
        const result = super.filterPaymentMethods(paymentMethods);
        const qpayMethods = paymentMethods.filter(
            (method) => method.use_payment_terminal === "qpay"
        );
        const existingIds = new Set(result.map((method) => method.id));

        for (const method of qpayMethods) {
            if (!existingIds.has(method.id)) {
                result.push(method);
            }
        }

        return result;
    },

    async confirmOrder() {
        const order = this.currentOrder;
        const device = this.config.self_ordering_mode;
        const service = this.selfService;

        if (
            device === "kiosk" &&
            service === "table" &&
            !order.isTakeaway &&
            !order.table_stand_number
        ) {
            const next =
                (parseInt(localStorage.getItem("qpay_kiosk_stand_counter") || "0", 10) % 999) +
                1;
            localStorage.setItem("qpay_kiosk_stand_counter", String(next));
            order.table_stand_number = String(next).padStart(3, "0");
        }

        return super.confirmOrder(...arguments);
    },
});

patch(PaymentPage.prototype, {
    setup() {
        super.setup();
        this.state.qpayQR = null;
        this.state.qpayShortUrl = null;
        this.state.qpayLoading = false;
        this.state.qpayError = null;
        this.state.qpayTransactionId = null;
        this.state.qpayOrderAccessToken = null;
        this.qpayPollToken = null;

        onWillUnmount(() => {
            this._stopQPayPolling();
        });
    },

    get isQPaySelected() {
        return (
            this.selectedPaymentMethod &&
            this.selectedPaymentMethod.use_payment_terminal === "qpay"
        );
    },

    _resetQPayState() {
        this.state.qpayQR = null;
        this.state.qpayShortUrl = null;
        this.state.qpayLoading = false;
        this.state.qpayError = null;
        this.state.qpayTransactionId = null;
        this.state.qpayOrderAccessToken = null;
    },

    _stopQPayPolling() {
        this.qpayPollToken = null;
    },

    async startPayment() {
        if (this.selectedPaymentMethod && this.selectedPaymentMethod.use_payment_terminal === "qpay") {
            await this._startQPayPayment();
            return;
        }
        return super.startPayment();
    },

    async _startQPayPayment() {
        this.selfOrder.paymentError = false;
        this._stopQPayPolling();
        this._resetQPayState();
        this.state.qpayLoading = true;

        try {
            const result = await rpc(`/kiosk/payment/${this.selfOrder.config.id}/kiosk`, {
                order: this.selfOrder.currentOrder.serializeForORM(),
                access_token: this.selfOrder.access_token,
                payment_method_id: this.state.paymentMethodId,
            });

            const paymentStatus = result && result.payment_status;
            const orderPayload = result && result.order;
            const orderData = Array.isArray(orderPayload)
                ? orderPayload[0]
                : orderPayload && orderPayload[0];

            this.state.qpayOrderAccessToken =
                (orderData && orderData.access_token) || null;

            if (paymentStatus && paymentStatus.status === "qpay_pending") {
                this.state.qpayQR = paymentStatus.qr_image || null;
                this.state.qpayShortUrl = paymentStatus.qpay_short_url || null;
                this.state.qpayTransactionId = paymentStatus.transaction_id || null;
                await this._pollQPayPayment();
            } else if (paymentStatus && paymentStatus.status === "error") {
                this.state.qpayError = paymentStatus.message || _t("QPay request failed.");
                this.selfOrder.paymentError = true;
            }
        } catch (error) {
            this.selfOrder.handleErrorNotification(error);
            this.selfOrder.paymentError = true;
        } finally {
            this.state.qpayLoading = false;
        }
    },

    async _pollQPayPayment() {
        const transactionId = this.state.qpayTransactionId;
        if (!transactionId) {
            return;
        }

        const pollToken = Symbol("qpay");
        this.qpayPollToken = pollToken;

        while (this.qpayPollToken === pollToken) {
            await delay(POLL_DELAY_MS);
            if (this.qpayPollToken !== pollToken) {
                return;
            }

            try {
                const result = await rpc("/qpay/kiosk/check", {
                    access_token: this.selfOrder.access_token,
                    payment_method_id: this.state.paymentMethodId,
                    transaction_id: transactionId,
                });

                if (result && result.status === "paid") {
                    this._stopQPayPolling();
                    await this._handleQPayPaid();
                    return;
                }

                if (result && result.status === "error") {
                    this.state.qpayError = result.message || _t("QPay payment failed.");
                    this.selfOrder.paymentError = true;
                    this._stopQPayPolling();
                    return;
                }
            } catch (error) {
                this.state.qpayError =
                    (error && error.data && error.data.message) ||
                    _t("Unable to poll QPay status.");
                this.selfOrder.paymentError = true;
                this._stopQPayPolling();
                return;
            }
        }
    },

    async _handleQPayPaid() {
        const orderAccessToken = this.state.qpayOrderAccessToken;
        if (!orderAccessToken) {
            window.location.reload();
            return;
        }

        await this.selfOrder.getOrdersFromServer([orderAccessToken]);
        this.selfOrder.notification.add(_t("Your order has been paid"), {
            type: "success",
        });
        this.selfOrder.confirmationPage(
            "order",
            this.selfOrder.config.self_ordering_mode,
            orderAccessToken
        );
    },

    async back() {
        const transactionId = this.state.qpayTransactionId;
        this._stopQPayPolling();

        if (transactionId) {
            try {
                await rpc("/qpay/kiosk/cancel", {
                    access_token: this.selfOrder.access_token,
                    payment_method_id: this.state.paymentMethodId,
                    transaction_id: transactionId,
                });
            } catch (error) {
                this.selfOrder.handleErrorNotification(error);
            }
        }

        this._resetQPayState();
        this.selfOrder.paymentError = false;
        return super.back();
    },
});
