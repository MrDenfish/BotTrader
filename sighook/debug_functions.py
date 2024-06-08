import pandas as pd
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from database_table_models import Trade, Holding


class DebugDataLoader:

    def __init__(self, db_tables, log_manager):

        self.log_manager = log_manager
        self.db_tables = db_tables

    async def get_trades(self, session: AsyncSession, asset):
        try:
            # Reflect the Trade table
            trade_table = Trade

            # Query all trades for the asset ordered by trade_time
            query = (
                select(trade_table)
                .where(trade_table.asset == asset)
                .order_by(trade_table.trade_time)
            )

            # Execute the query and fetch all results
            result = await session.execute(query)
            rows = result.fetchall()

            # Extract attributes from Trade objects and convert to DataFrame
            trades_list = []
            for row in rows:
                trade = row[0]
                if trade.amount == 0:
                    self.log_manager.sighook_logger.warning(f'Zero amount trade: {trade.trade_id}, '
                                                            f'{trade.trade_time} get_trades')
                trades_list.append({
                    'trade_id': trade.trade_id,
                    'order_id': trade.order_id,
                    'trade_time': trade.trade_time,
                    'transaction_type': trade.transaction_type,
                    'asset': trade.asset,
                    'amount': trade.amount,
                    'currency': trade.currency,
                    'price': trade.price,
                    'cost': trade.cost,
                    'proceeds': trade.proceeds,
                    'fee': trade.fee,
                    'total': trade.total,
                    'notes': trade.notes
                })

            df = pd.DataFrame(trades_list)
            # Display trades with zero amounts
            if df.empty:

                return pd.DataFrame()
            zero_amount_trades = df[df['amount'] == 0]
            # print(zero_amount_trades.to_string(index=False))

            # Display trades with unusual total values
            unusual_totals = df[(df['total'] == 0) & (df['transaction_type'] == 'buy')]
            # print(unusual_totals.to_string(index=False))

            # Check for duplicate order IDs
            duplicate_trade_ids = df[df.duplicated(subset=['trade_id'], keep=False)]
            # print(duplicate_trade_ids.to_string(index=False))
            # Log the DataFrame for debugging
            print(df.head(20).to_string(index=False))
            self.log_manager.sighook_logger.debug(f'Trades DataFrame:\n{df.head()}')

            return df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'get_trades error: {e}', exc_info=True)
            return pd.DataFrame()  # Return an empty DataFrame in case of error

    async def get_holdings(self, session: AsyncSession):
        try:
            # Reflect the Holding table
            holding_table = Holding

            # Query all holdings ordered by purchase_date
            query = (
                select(holding_table)
                .order_by(holding_table.purchase_date)
            )

            # Execute the query and fetch all results
            result = await session.execute(query)
            rows = result.fetchall()

            # Extract attributes from Holding objects and convert to DataFrame
            holdings_list = []
            for row in rows:
                holding = row[0]
                holdings_list.append({
                    'currency': holding.currency,
                    'asset': holding.asset,
                    'purchase_date': holding.purchase_date,
                    'purchase_price': holding.purchase_price,
                    'current_price': holding.current_price,
                    'purchase_amount': holding.purchase_amount,
                    'initial_investment': holding.initial_investment,
                    'market_value': holding.market_value,
                    'balance': holding.balance,
                    'weighted_average_cost': holding.weighted_average_cost,
                    'unrealized_profit_loss': holding.unrealized_profit_loss,
                    'unrealized_pct_change': holding.unrealized_pct_change
                })

            df = pd.DataFrame(holdings_list)

            # Display holdings with zero balances
            zero_balance_holdings = df[df['balance'] == 0]
            # print(zero_balance_holdings.to_string(index=False))

            # Display holdings with negative unrealized profit/loss
            negative_unrealized_pl = df[df['unrealized_profit_loss'] < 0]
            # print(negative_unrealized_pl.to_string(index=False))

            # Check for duplicate asset and currency pairs
            duplicate_holdings = df[df.duplicated(subset=['currency', 'asset'], keep=False)]
            # print(duplicate_holdings.to_string(index=False))

            # Log the DataFrame for debugging
            self.log_manager.sighook_logger.debug(f'Holdings DataFrame:\n{df.head()}')

            return df
        except Exception as e:
            self.log_manager.sighook_logger.error(f'get_holdings error: {e}', exc_info=True)
            return pd.DataFrame()  # Return an empty DataFrame in case of error
