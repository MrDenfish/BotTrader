""" The project is python based and it is rather complex (in my view) it is called 'BotTraderv3.0'.
The project directory has several distinct subdirectories: Api_manager, logs, sighook, utils,
webhook, an env file as well as docker compose files. There is also a database 'bot_trader_db'
that consists of (8) tables.  sighook has several modules and it's primary responsibilities
are to manage the database, compute trading strategies and send webhook signals to webhook.
webhook's primary responsibilities are to listen for webhook signals, interact with trading
exchange using websocket,  process webhook  and websocket signals and to place orders with
the exchange.  The database stores all trades from all traded currencies in the table 'trades'.
At this point the program is running on a mac desktop computer, but will eventually run inside
a docker container on digital ocean."""

"""Part I. Data Gathering and Database Loading downloads initial trades from the exchange as well
 as MarketDataManager. Trade data is stored in the database and MarketDataManager is stored as a dictionary  
 of dataframes and takes about 3:47 to download and process, Part II. retrieves portfolio data 
 from the exchange, calculates a dollar volume, creates and loads a buy sell matrix and takes 
 about 3 minutes to complete. Part III. Deals with order cancelation and data collection , it 
 takes about 8 minutes to perform. Part IV.  Is where trading strategies are calculated, this 
 section takes 2 minutes to perform. Part V. is the part where orders are executed, this section 
 just a few seconds. Part VI. is where a profitability analysis is performed and this section 
 takes about 3.5 minutes to perform. The entire program takes about 20 minutes to run one iteration, 
 which seems like too much time."""

