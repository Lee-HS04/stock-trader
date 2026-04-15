# NOTE: THIS EXECUTOR IS A PAPER TRADER. IT CURRENTLY SIMULATES A TRADING ACCOUNT USING JSON DATA AND DOES NOT ACTUALLY CONNECT VIA A REAL BROKERAGE API

import sqlite3 as sl3
import json 
import os
import argparse
from datetime import datetime

# This is the simulated trading account
account = "account.json"
database = "trade_history.db"

def load_account():
    if os.path.exists(account):
        file = open(account, "r")
        portfolio = json.load(file)
        file.close()
    else:
        # if no portfolio, make one
        file = open(account, "w")
        portfolio = {"balance": 100000.00, "stocks": {}}
        json.dump(portfolio, file)
        file.close()
        
    db = sl3.connect(database)
    cursor = db.cursor()
    cursor.execute("""CREATE TABLE IF NOT EXISTS trade_history(
        trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
        firm TEXT,
        price FLOAT,
        quantity INTEGER,
        action TEXT,
        timestamp TEXT
        )""")
    db.commit()
    db.close()
    return portfolio
    
def trade(firm, price, quantity, action):
    portfolio = load_account()
    if action == "buy":
        balance = portfolio["balance"] - float((price*quantity),2)
        if balance <0:
            print ("Invalid: Insufficient balance")
            return
        portfolio["balance"] = balance
        portfolio["stocks"][firm] = portfolio["stocks"].get(firm, 0) + quantity
        
    elif action == "sell":
        stock = portfolio["stocks"].get(firm, 0)
        if stock < quantity:
            print(f"Invalid: Not enough {firm} stock in portfolio")
            return
        else:
            portfolio["stocks"][firm] -= quantity
            portfolio["balance"] = portfolio["balance"] + float((price*quantity),2)
    
    # Use (?,?,?) as placeholder to prevent SQL input injection attack
    db = sl3.connect(database)
    cursor = db.cursor()
    timestamp = datetime.now().isoformat()
    cursor.execute("INSERT INTO trade_history (firm, price, quantity, action, timestamp) VALUES (?,?,?,?,?)", (firm, price, quantity, action, timestamp))  
    db.commit()
    db.close()
    with open(account, "w") as file:
        json.dump(portfolio, file)  
    print(f"Success: {action} {quantity} {firm} stocks at {price} per share. Balance: {portfolio['balance']}")
    return 

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--firm", type=str, help="Enter firm whose stock you want to buy", required=True)
    parser.add_argument("--price", type=float, help="Enter price of that stock", required=True)
    parser.add_argument("--quantity", type=float, help="Enter the number of stocks you want to buy/sell", required=True)
    parser.add_argument("--action", type=str, help= "Please enter either \"buy\" or \"sell\"", choices=["buy","sell"], required= True)
    args = parser.parse_args()
    trade(args.firm, args.price, args.quantity, args.action)
    
    