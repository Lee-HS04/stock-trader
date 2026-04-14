from massive import RESTClient
import pandas_ta as pt
import argparse
import json
import os
from datetime import datetime
from dateutil.relativedelta import relativedelta

API_KEY = os.getenv("D7sOI2RyluFFXXRe2GuGhsalUl39CzJg") #using environment variable to increase security
client = RESTClient(API_KEY)

def get_historical_data(firm, unit = "years", value = "1"):
    prd = {unit: value}
    from_date = datetime.now() - relativedelta(**prd)
    to_date = datetime.now()
    aggs = client.get_aggs(firm, 1, "day", from_date, to_date)
    information = pt.DataFrame([dict(x) for x in aggs])
    information['timestamp'] =pt.to_datetime(information['timestamp'], unit = 'ms')
    information.set_index('timestamp', implace=True)
    return information

def calculate_indicators(firm, info, rsi=14, ema = 20, sma = 100): 
    #14 stands for 14 periods. This is the industry standard for RSI calculation
    #the optimal ma/ema period depends on what the user trend the user wants to capture. If they want fast trends, 10 to 20 is the standard. If they want macro trends, 50, 100 and 200 are standard.
    info['RSI'] = pt.rsi(info['Close'], length=rsi) #info['Close'] is the column for the closing price. RSI uses closing price in calculation. Closing price is the final price at which a stock is traded during a trading session
    info['EMA'] = pt.ema(info['Close'], length=ema) #we use ema instead of sma for short term as ema is more responsive. ma weighs all the prices in the period equally, while ema weighs the most recent prices more heavily.
    # For example, if a stock were to perform very well in the last 49 days but suddenly crash ytd, ma will ignore it by smoothing it out due to weighing all the prices equally.
    # ema will capture this crash as it gives the heaviest weight to the ytd's price. In stock trading, traders react immediately when such an event happens. We want to make sure our agent is able to do so as well
    info['SMA'] = pt.sma(info['Close'], length=sma) #for long term we use sma instead as we want to see the structural trend of the industry.
    # A sudden fluctuation such as a crash in a firm's stock is usually not representative of the industry's performance over the years. Therefore, we want to make sure we ignore this when calculating long term trend.
    
    # return a json with latest closing price, RSI, EMA, and SMA to the LLM brain. 
    return json.dumps({"data":{"latest closing price": float(info['Close'].iloc[-1]), "latest RSI": float(info['RSI'].iloc[-1]), "latest EMA": float(info['EMA'].iloc[-1]), "latest SMA": float(info['SMA'].iloc[-1])}, "time_period": f"These data were taken from the last 1 year of {firm} stock."})

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--firm", type=str, help="Enter firm whose stock you are interested in", required=True)
    parser.add_argument("--unit", type=str, help="Enter the unit of time you want to get the data for. e.g. if you want data from one year ago, enter \"years\", and enter 1 for --period")
    parser.add_argument("--period", type=str, help="Enter the period of time you want to get the data for. e.g. if you want data from the last year, enter 1yr. Feel free to leave this blank")
    parser.add_argument("--rsi", type=int, help="Enter the number of periods you want to use for RSI calculation. If you are not sure, industry standard is 14 periods. Feel free to leave this blank")
    parser.add_argument("--ema", type=int, help="Enter the number of periods you want to use for EMA calculation. Feel free to leave this blank")
    parser.add_argument("--sma", type=int, help="Enter the number of periods you want to use for SMA calculation. Feel free to leave this blank")
    args = parser.parse_args()
    firm = args.firm
    
    # retrive information
    info = get_historical_data(firm, args.unit, args.period)
    # print data (this will be given to an LLM brain)
    print(calculate_indicators(firm, info, args.rsi, args.ema, args.sma))