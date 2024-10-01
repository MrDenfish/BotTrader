
from sqlalchemy.ext.asyncio import  AsyncSession
from sqlalchemy.future import select
from sqlalchemy.sql import func
from sqlalchemy import case, delete
from collections import defaultdict
from database_table_models import Trade, Holding
from decimal import Decimal
import pandas as pd

class DatabaseOpsHoldingsManager:

    def __init__(self, log_manager, async_session_factory, *args, **kwargs):
        self.log_manager = log_manager
        self.AsyncSessionLocal = async_session_factory

    @staticmethod
    async def clear_holdings(session: AsyncSession):
        """Clear all entries in the holdings table."""
        await session.execute(delete(Holding))
        await session.commit()

    @staticmethod
    async def get_updated_holdings(session):
        """PART VI: Profitability Analysis and Order Generation  Fetch the updated contents of the holdings table using
        the provided session."""
        result = await session.execute(select(Holding))
        return result.scalars().all()

    async def initialize_holding_db(self, session, holdings_list, holdings_df, current_prices, sell_orders=None,
                                    open_orders=None):
        """Handle the initialization or update of holdings in the database based on provided data."""
        try:
            sell_orders_exist = sell_orders is not None and len(sell_orders) > 0
            open_orders_exist = open_orders is not None and len(open_orders) > 0

            # Process sell_orders first
            if sell_orders_exist:
                for (asset, sell_amount, sell_price, holding) in sell_orders:
                    await self.update_single_holding(session, holding, current_prices)

            # Process open_orders next
            if open_orders_exist:
                # Add 'asset' column to open_orders DataFrame
                open_orders['asset'] = open_orders['product_id'].str.split('-').str[0]

                # Iterate over each row in 'holdings' DataFrame
                for _, holding in holdings_df.iterrows():
                    # Filter open orders by holding asset
                    asset_orders = open_orders[open_orders['asset'] == holding['asset']]
                    # trailing_stop = asset_orders[asset_orders['trigger_status'] == 'STOP_PENDING'] # Not currently used
                    await self.update_single_holding(session, holding, current_prices)

            # If only holdings are provided without any sell_orders or open_orders
            if not sell_orders_exist and not open_orders_exist:
                for _, holding in holdings_df.iterrows():
                    await self.update_single_holding(session, holding, current_prices)

        except Exception as e:
            self.log_manager.error(f'initialize_holding_db: {e}', exc_info=True)
            await session.rollback()

    async def update_single_holding(self, session, holding, current_prices, open_orders=None, sell_orders=None):
        """PART V: Order Execution"""
        """PART VI: Profitability Analysis and Order Generation"""
        try:
            trailing_stop = 0
            aggregated_data = await self.aggregate_trade_data_for_symbol(session, holding['asset'])
            if aggregated_data is None:
                return None

            # If open_orders is provided, process them
            if open_orders is not None:
                open_orders['asset'] = open_orders['product_id'].str.split('-').str[0]
                # Filter open orders by holding asset
                asset_orders = open_orders[open_orders['asset'] == holding['asset']]
                # Check for STOP_PENDING status
                stop_pending_orders = asset_orders[asset_orders['trigger_status'] == 'STOP_PENDING']

                if len(stop_pending_orders) > 0:
                    trailing_stop = stop_pending_orders
                    # Convert trailing_stop to a string or a primitive type for holding db
                    if isinstance(trailing_stop, pd.DataFrame):
                        trailing_stop = trailing_stop.to_json()  # Convert to JSON string or a suitable format
                else:
                    trailing_stop = 0

            # If sell_orders is provided, adjust the holding accordingly
            if sell_orders is not None:
                for (asset, sell_amount, sell_price, sell_holding) in sell_orders:
                    if asset == holding['asset']:
                        # Adjust balance and calculate the new market value and profit/loss
                        holding['balance'] -= sell_amount
                        holding['unrealized_profit_loss'] = (sell_price - aggregated_data['purchase_price']) * sell_amount
                        holding['unrealized_pct_change'] = (sell_price - aggregated_data['purchase_price']) / \
                                                           aggregated_data['purchase_price'] * 100

            stmt = select(Holding).where(Holding.asset == holding['asset'], Holding.currency == holding['quote'])
            result = await session.execute(stmt)
            existing_holding = result.scalars().first()
            symbol = holding['asset'] + '/' + holding['quote']
            current_price = Decimal(current_prices.get(symbol))

            if not existing_holding:
                # Create new holding if it does not exist
                new_holding = Holding(
                    currency=holding['quote'],
                    asset=holding['asset'],
                    purchase_date=aggregated_data['most_recent_trade_time'],
                    purchase_price=aggregated_data['purchase_price'],  # Default to 0 if not found
                    current_price=current_price,  # Default to 0 if not found
                    purchase_amount=Decimal(holding.get('total', 0)),
                    initial_investment=Decimal(aggregated_data['purchase_price']) * Decimal(holding['total']),
                    market_value=Decimal(holding['total']) * Decimal(current_prices.get(holding['symbol'], 0)),
                    balance=Decimal(holding['balance']),
                    weighted_average_price=aggregated_data['weighted_average_price'],
                    unrealized_profit_loss=(holding.get('unrealized_profit_loss', 0)),
                    unrealized_pct_change=holding.get('unrealized_pct_change', 0),
                    trailing_stop=trailing_stop
                )
                session.add(new_holding)
            else:
                # Get from current_prices or fallback to existing
                update_fields = {
                    'purchase_date': aggregated_data['most_recent_trade_time'],
                    'purchase_price': aggregated_data['purchase_price'],
                    'balance': Decimal(holding['balance']),
                    'current_price': current_price,
                    # Use existing price as fallback
                    'initial_investment': aggregated_data['purchase_price'] * Decimal(holding['total']),
                    'weighted_average_price': aggregated_data.get('weighted_average_price',
                                                                 existing_holding.weighted_average_price),
                    'market_value': Decimal(holding['total']) * Decimal(current_price),
                    'unrealized_profit_loss': holding.get('unrealized_profit_loss', existing_holding.unrealized_profit_loss),
                    'unrealized_pct_change': holding.get('unrealized_pct_change', existing_holding.unrealized_pct_change),
                    'trailing_stop': trailing_stop
                }
                for key, value in update_fields.items():
                    setattr(existing_holding, key, value)

        except Exception as e:
            self.log_manager.error(f'update_single_holding: {e}', exc_info=True)
            await session.rollback()  # Roll back only the current holding processing

    async def aggregate_trade_data_for_symbol(self, session: AsyncSession, asset: str):
        """PART VI: Profitability Analysis and Order Generation """
        try:
            # Fetch the most recent trade time for the asset
            most_recent_time_result = await session.execute(
                select(func.max(Trade.trade_time)).filter(Trade.asset == asset)
            )
            most_recent_time = most_recent_time_result.scalar()

            # Aggregate trade data from the trades table
            aggregation_query = (
                select(
                    func.min(Trade.trade_time).label('earliest_trade_time'),
                    func.max(Trade.trade_time).label('most_recent_trade_time'),
                    func.sum(case((Trade.amount > 0, Trade.amount), else_=0)).label('total_purchase_amount'),
                    func.sum(case((Trade.amount < 0, Trade.amount), else_=0)).label('sold_amount'),
                    func.sum(Trade.total).label('total'),
                    func.sum(Trade.amount).label('amount'),
                    func.sum(Trade.balance).label('balance'),
                    func.sum(Trade.cost).label('cost'),
                    func.sum(Trade.fee).label('fee'),
                    func.sum(Trade.proceeds).label('proceeds'),
                    func.sum(case((Trade.amount != 0, Trade.total), else_=0)).label('initial_investment'),
                )
                .filter(Trade.asset == asset)
                .group_by(Trade.asset)
            )
            aggregation_result = await session.execute(aggregation_query)
            aggregation = aggregation_result.one_or_none()

            # Fetch the most recent non-zero cost entry for the asset
            most_recent_cost_query = (
                select(Trade)
                .filter(Trade.asset == asset, Trade.cost < 0)
                .order_by(Trade.trade_time.desc())
                .limit(1)
            )
            most_recent_buy_result = await session.execute(most_recent_cost_query)
            most_recent_buy = most_recent_buy_result.scalar_one_or_none()

            # Fetch all trades for the asset to verify totals
            all_trades_query = (
                select(Trade)
                .filter(Trade.asset == asset)
                .order_by(Trade.trade_time)
            )
            all_trades_result = await session.execute(all_trades_query)
            all_trades = all_trades_result.fetchall()

            # Aggregate trades by order_id
            trades_by_order = defaultdict(lambda: {'amount': 0.0, 'cost': 0.0, 'proceeds': 0.0})

            for trade_tuple in all_trades:
                trade = trade_tuple[0]
                trades_by_order[trade.order_id]['amount'] += float(trade.amount)
                trades_by_order[trade.order_id]['cost'] += float(trade.cost)
                trades_by_order[trade.order_id]['proceeds'] += float(trade.proceeds)

            total_amount = sum(order['amount'] for order in trades_by_order.values())
            total_cost = sum(order['cost'] for order in trades_by_order.values())

            self.log_manager.debug(f'Total amount for all trades: {total_amount}')
            self.log_manager.debug(f'Total cost for all trades: {total_cost}')

            total_purchase_amount = sum(order['amount'] for order in trades_by_order.values() if order['amount'] > 0)
            sold_amount = sum(order['amount'] for order in trades_by_order.values() if order['amount'] < 0)

            self.log_manager.debug(f'Purchase amount for all trades: {total_purchase_amount}')
            self.log_manager.debug(f'Sold amount for all trades: {sold_amount}')

            if aggregation and aggregation.total_purchase_amount and aggregation.total_purchase_amount > 0:
                # Calculate weighted average cost
                if total_purchase_amount > 0:
                    weighted_average_price = total_cost / total_purchase_amount
                else:
                    weighted_average_price = 0

                # Calculate net balance and balance
                net_balance = aggregation.total_purchase_amount + aggregation.sold_amount
                balance = aggregation.balance

                # Fetch the purchase price from the most recent non-zero cost entry
                purchase_price = most_recent_buy.price if most_recent_buy else 0

                # Calculate initial investment using the total cost from trades
                initial_investment = total_cost

                return {
                    'earliest_trade_time': aggregation.earliest_trade_time,
                    'most_recent_trade_time': aggregation.most_recent_trade_time,
                    'purchase_amount': aggregation.total_purchase_amount,
                    'sold_amount': aggregation.sold_amount,
                    'initial_investment': initial_investment,
                    'net_balance': net_balance,
                    'balance': balance,
                    'market_value': total_cost,
                    'purchase_price': Decimal(purchase_price),
                    'weighted_average_price': weighted_average_price,
                    'entry_price': purchase_price,
                }
            else:
                # Log if no valid trades found
                self.log_manager.debug(
                    f"No valid trades found for asset {asset}. Total amount: "
                    f"{aggregation.total_purchase_amount if aggregation else 'None'}"
                )

                return None
        except Exception as e:
            self.log_manager.error(f'aggregate_trade_data_for_symbol error: {e}', exc_info=True)
            return None

