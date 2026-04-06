/** @odoo-module **/

import { patch } from "@web/core/utils/patch";
import { PaymentPage } from "@pos_self_order/app/pages/payment_page/payment_page";
import { SelfOrder } from "@pos_self_order/app/services/self_order_service";
import { rpc } from "@web/core/network/rpc";

// ─── 1. SelfOrder сервист QPay method-г нэмнэ ───────────────────────────────

patch(SelfOrder.prototype, {
    /**
     * Kiosk mode-д QPay payment method-г ч гэсэн харуулна.
     */
    filterPaymentMethods(pms) {
        const result = super.filterPaymentMethods(pms);
        const qpayMethods = pms.filter((pm) => pm.use_payment_terminal === "qpay");
        // Давхардлаас зайлсхийна
        const existingIds = new Set(result.map((m) => m.id));
        for (const pm of qpayMethods) {
            if (!existingIds.has(pm.id)) {
                result.push(pm);
            }
        }
        return result;
    },

    /**
     * Kiosk / table service дээр tracker number автоматаар оноох.
     * Хэрэглэгч "Enter tracker number" хуудсыг харахгүй.
     */
    async confirmOrder() {
        const order = this.currentOrder;
        const device = this.config.self_ordering_mode; // 'kiosk'
        const service = this.selfService; // 'table', 'counter', 'delivery'

        if (
            device === "kiosk" &&
            service === "table" &&
            !order.isTakeaway &&
            !order.table_stand_number
        ) {
            // Дараалсан тоо localStorage-д хадгална (0–999 эргэлдэнэ)
            const next =
                (parseInt(localStorage.getItem("qpay_kiosk_stand_counter") || "0") % 999) + 1;
            localStorage.setItem("qpay_kiosk_stand_counter", String(next));
            order.table_stand_number = String(next).padStart(3, "0");
        }

        return super.confirmOrder(...arguments);
    },
});

// ─── 2. PaymentPage-д QPay QR харуулах ──────────────────────────────────────

patch(PaymentPage.prototype, {
    setup() {
        super.setup();
        // QPay төлөвийн нэмэлт state талбарууд
        this.state.qpayQR = null;
        this.state.qpayShortUrl = null;
        this.state.qpayLoading = false;
        this.state.qpayError = null;
    },

    get isQPaySelected() {
        return this.selectedPaymentMethod?.use_payment_terminal === "qpay";
    },

    async startPayment() {
        const method = this.selectedPaymentMethod;
        if (method?.use_payment_terminal === "qpay") {
            await this._startQPayPayment();
            return;
        }
        return super.startPayment();
    },

    async _startQPayPayment() {
        this.selfOrder.paymentError = false;
        this.state.qpayQR = null;
        this.state.qpayError = null;
        this.state.qpayLoading = true;

        try {
            const result = await rpc(
                `/kiosk/payment/${this.selfOrder.config.id}/kiosk`,
                {
                    order: this.selfOrder.currentOrder.serializeForORM(),
                    access_token: this.selfOrder.access_token,
                    payment_method_id: this.state.paymentMethodId,
                }
            );

            const ps = result?.payment_status;
            if (ps?.status === "qpay_pending") {
                this.state.qpayQR = ps.qr_image || null;
                this.state.qpayShortUrl = ps.qpay_short_url || null;
            } else if (ps?.status === "error") {
                this.state.qpayError = ps.message || "QPay алдаа гарлаа";
                this.selfOrder.paymentError = true;
            }
        } catch (error) {
            this.selfOrder.handleErrorNotification(error);
            this.selfOrder.paymentError = true;
        } finally {
            this.state.qpayLoading = false;
        }
    },

    back() {
        // Буцах үед QPay төлөвийг цэвэрлэнэ
        this.state.qpayQR = null;
        this.state.qpayShortUrl = null;
        this.state.qpayError = null;
        this.state.qpayLoading = false;
        super.back();
    },
});
