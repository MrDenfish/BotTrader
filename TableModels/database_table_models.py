from models.market_snapshot import MarketDataSnapshot
from models.ohlcv_data import OHLCVData
from models.order_management import OrderManagementSnapshot


class DatabaseTables:
    def __init__(self):
        # Register each model as an attribute
        self.OHLCVData = OHLCVData
        self.MarketDataSnapshot = MarketDataSnapshot
        self.OrderManagementSnapshot = OrderManagementSnapshot
