/** @odoo-module **/

import {
    Component,
    useState,
    onWillStart,
    onMounted,
    onWillUnmount,
} from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

// ─── Helper utilities ────────────────────────────────────────────────────────

/**
 * Format an ISO datetime string as a short local time (HH:MM:SS).
 */
function formatTime(isoString) {
    if (!isoString) return "";
    try {
        // Odoo sends datetimes as "YYYY-MM-DD HH:MM:SS" (server time / UTC).
        // Convert to a JS Date – treat as UTC by appending 'Z'.
        const normalized = isoString.replace(" ", "T") + "Z";
        return new Date(normalized).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
        });
    } catch {
        return isoString;
    }
}

// ─── Main KitchenDisplay Component ───────────────────────────────────────────

export class KitchenDisplay extends Component {
    static template = "pos_kitchen_display.KitchenDisplay";
    // Accept any props passed by the action framework
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.busService = useService("bus_service");

        // Bind action handlers so `this` is preserved when called
        // from t-call sub-templates (OWL 2 loses context otherwise)
        this.onMarkInProgress = this.onMarkInProgress.bind(this);
        this.onMarkDone = this.onMarkDone.bind(this);
        this.onMarkPending = this.onMarkPending.bind(this);
        this.toggleShowDone = this.toggleShowDone.bind(this);

        this.state = useState({
            orders: [],
            loading: true,
            showDone: false,
            lastUpdate: "",
            clock: "",
        });

        this._refreshInterval = null;
        this._clockInterval = null;
        this._busHandler = this._onBusMessage.bind(this);

        onWillStart(async () => {
            await this._loadOrders();
        });

        onMounted(() => {
            // ── WebSocket: subscribe to kitchen_display channel ──────────
            this.busService.addChannel("kitchen_display");
            this.busService.subscribe("kitchen_order_update", this._busHandler);

            // ── Polling fallback: reload every 5 seconds ─────────────────
            this._refreshInterval = setInterval(() => {
                this._loadOrders();
            }, 5000);

            // ── Live clock ───────────────────────────────────────────────
            this._updateClock();
            this._clockInterval = setInterval(() => this._updateClock(), 1000);
        });

        onWillUnmount(() => {
            clearInterval(this._refreshInterval);
            clearInterval(this._clockInterval);
            this.busService.unsubscribe("kitchen_order_update", this._busHandler);
            try {
                this.busService.deleteChannel("kitchen_display");
            } catch {
                // channel may already be gone
            }
        });
    }

    // ─── Data loading ─────────────────────────────────────────────────────

    async _loadOrders() {
        try {
            const orders = await this.orm.call(
                "kitchen.order",
                "get_kitchen_orders_data",
                [],
                { include_done: this.state.showDone }
            );
            this.state.orders = orders;
            this.state.lastUpdate = new Date().toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
            });
        } catch (err) {
            console.error("[KitchenDisplay] Failed to load orders:", err);
        } finally {
            this.state.loading = false;
        }
    }

    _onBusMessage(_payload) {
        // Any bus notification triggers a reload
        this._loadOrders();
    }

    _updateClock() {
        this.state.clock = new Date().toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
        });
    }

    // ─── Order actions ────────────────────────────────────────────────────

    async onMarkInProgress(orderId) {
        await this.orm.call("kitchen.order", "action_in_progress", [[orderId]]);
        await this._loadOrders();
    }

    async onMarkDone(orderId) {
        await this.orm.call("kitchen.order", "action_done", [[orderId]]);
        await this._loadOrders();
    }

    async onMarkPending(orderId) {
        await this.orm.call("kitchen.order", "action_reset_pending", [[orderId]]);
        await this._loadOrders();
    }

    toggleShowDone() {
        this.state.showDone = !this.state.showDone;
        this._loadOrders();
    }

    // ─── Template helpers ─────────────────────────────────────────────────

    get pendingOrders() {
        return this.state.orders.filter((o) => o.status === "pending");
    }

    get inProgressOrders() {
        return this.state.orders.filter((o) => o.status === "in_progress");
    }

    get doneOrders() {
        return this.state.orders.filter((o) => o.status === "done");
    }

    /**
     * CSS class for the elapsed timer based on urgency.
     */
    elapsedClass(order) {
        const m = order.elapsed_minutes || 0;
        if (order.status === "done") return "ok";
        if (m >= 20) return "urgent";
        if (m >= 10) return "warning";
        return "ok";
    }

    /**
     * Human-readable elapsed time string.
     */
    formatElapsed(minutes) {
        if (!minutes && minutes !== 0) return "—";
        if (minutes < 1) return "< 1 min";
        if (minutes === 1) return "1 min";
        return `${minutes} min`;
    }

    /**
     * Quantity display — show integer when possible.
     */
    formatQty(qty) {
        if (qty === undefined || qty === null) return "?";
        return Number.isInteger(qty) ? String(qty) : qty.toFixed(1);
    }
}

// Register as a client action
registry.category("actions").add("kitchen_display_action", KitchenDisplay);
