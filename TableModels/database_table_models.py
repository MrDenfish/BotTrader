from TableModels.market_snapshot import MarketDataSnapshot
from TableModels.ohlcv_data import OHLCVData
from TableModels.order_management import OrderManagementSnapshot




class DatabaseTables:
    def __init__(self):
        # Register each model as an attribute
        self.OHLCVData = OHLCVData
        self.MarketDataSnapshot = MarketDataSnapshot
        self.OrderManagementSnapshot = OrderManagementSnapshot

