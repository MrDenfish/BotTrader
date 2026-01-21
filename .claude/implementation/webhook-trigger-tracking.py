# Webhook Trigger Tracking Implementation
# Add this to webhook/listener.py after successful buy order execution

# In the handle_buy_signal() or similar method, after order is successfully placed:

# Store trigger type in shared_data_manager for position_monitor to access
if success and 'order_id' in response:
    trigger_type = trade_data.get('trigger')  # e.g., 'roc_momo', 'score', etc.
    product_id = trade_data.get('trading_pair')  # e.g., 'EDGE-USD'

    # Initialize position_triggers dict if needed
    if 'position_triggers' not in self.shared_data_manager.order_management:
        self.shared_data_manager.order_management['position_triggers'] = {}

    # Store trigger type for this position
    if trigger_type:
        self.shared_data_manager.order_management['position_triggers'][product_id] = trigger_type
        self.logger.info(
            f"[TRIGGER_TRACK] Stored trigger for {product_id}: {trigger_type}"
        )

# When position is fully closed, clean up the trigger metadata:
# self.shared_data_manager.order_management['position_triggers'].pop(product_id, None)
