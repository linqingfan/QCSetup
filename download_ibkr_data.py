from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import *
import datetime
import time
import pandas as pd
import threading
import pytz
import os
import json

# --- Global Variables ---
bar_collection = {}
config = {}

# --- Constants ---
# Codes that indicate a TRUE connection issue requiring a client-level reconnect
RETRYABLE_ERROR_CODES = {
    1100,  # Connectivity between IB and TWS has been lost.
    1101,  # Connectivity between IB and TWS has been restored- data lost.
    1102,  # Connectivity between IB and TWS has been restored- data maintained.
    1300,  # Socket port has been reset and TWS is not receiving commands.
    2103,  # A market data farm is not responding. (More severe than "OK" or "inactive")
    2105,  # A historical data farm is not responding. (More severe)
    # 502, 504, 509 are often caught by app.isConnected() or initial connection failure
}

# Codes specific to a request that might be retried after a delay
REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES = {
    162,  # Historical Market Data Service error message:Historical data request pacing violation
    321,  # Error validating request
    322,  # Error processing request (too many simultaneous)
}

# Informational codes that should be logged but NOT trigger a full reconnect
INFORMATIONAL_API_CODES = {
    2100,  # API client has been unsubscribed from account data.
    2104,  # Market data farm connection is OK
    2106,  # HMDS data farm connection is OK
    2107,  # HMDS data farm connection is inactive but should be available upon demand.
    2108,  # Market data farm connection has become inactive but should be available shortly.
    2150,  # Invalid account code specified.
    2158,  # Sec-def data farm connection is OK
}
CSV_WRITE_ERROR_CODE = 999  # Custom code for CSV write errors


class IBapi(EWrapper, EClient):
    def __init__(self, filename: str, expected_timezone_str: str = "America/New_York"):
        EClient.__init__(self, self)
        self.req_id_counter = 0
        self.filename = filename
        try:
            self.expected_timezone = pytz.timezone(expected_timezone_str)
        except pytz.exceptions.UnknownTimeZoneError:
            print(f"Warning: Unknown timezone '{expected_timezone_str}'. Defaulting to UTC.")
            self.expected_timezone = pytz.utc
        self.connection_lost = False
        self.is_connected_event = threading.Event()
        self.next_valid_req_id_ready = threading.Event()

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.req_id_counter = orderId
        print(f"NextValidId: {orderId}. Connection confirmed. Ready for requests.")
        self.is_connected_event.set()
        self.next_valid_req_id_ready.set()
        self.connection_lost = False # Reset on successful (re)connection and nextValidId

    def get_next_req_id(self):
        self.next_valid_req_id_ready.wait()
        current_id = self.req_id_counter
        self.req_id_counter += 1
        return current_id

    def historicalData(self, reqId: int, bar: BarData):
        t_str_original = bar.date
        t_parts = bar.date.split(' ')
        processed_datetime_utc = None

        if len(t_parts) > 1:
            datetime_str_component = t_parts[0] + ' ' + t_parts[1]
            try:
                naive_datetime = datetime.datetime.strptime(datetime_str_component, '%Y%m%d %H:%M:%S')
                if len(t_parts) == 3:
                    ibkr_timezone_str = t_parts[2]
                    try:
                        ibkr_timezone = pytz.timezone(ibkr_timezone_str)
                        localized_datetime = ibkr_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                    except pytz.exceptions.UnknownTimeZoneError:
                        localized_datetime = self.expected_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                else:
                    localized_datetime = self.expected_timezone.localize(naive_datetime)
                    processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error parsing datetime string component '{datetime_str_component}' from '{t_str_original}': {e}. Skipping bar.")
                return
        else:
            try:
                naive_datetime = datetime.datetime.strptime(t_parts[0], '%Y%m%d')
                localized_datetime = self.expected_timezone.localize(naive_datetime)
                processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error parsing date string component '{t_parts[0]}' from '{t_str_original}': {e}. Skipping bar.")
                return
        
        if processed_datetime_utc is None:
            print(f"Could not process timestamp: {t_str_original}. Skipping bar.")
            return

        if reqId in bar_collection:
            data = {
                'date': processed_datetime_utc,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': int(bar.volume)
            }
            bar_collection[reqId]['data'].append(data)
        else:
            print(f"Warning: Received historicalData for unknown reqId: {reqId}. Bar ignored.")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        if reqId not in bar_collection:
            print(f"Warning: historicalDataEnd received for unknown reqId: {reqId}. No action taken.")
            return
        
        if not bar_collection[reqId]['data']:
            bar_collection[reqId]['status'] = "complete" # No data, but request is done
            return

        df = pd.DataFrame(bar_collection[reqId]['data'])
        
        if df.empty:
            bar_collection[reqId]['status'] = "complete"
            return

        if 'date' not in df.columns:
            print(f"Error: 'date' column missing in DataFrame for ReqId: {reqId}. Marking as failed_permanent.")
            bar_collection[reqId]['status'] = "failed_permanent"
            bar_collection[reqId]['last_error_code'] = "INTERNAL_DATA_FORMAT_ERROR"
            return
            
        df = df.sort_values('date')
        df.drop_duplicates(subset=['date'], keep='first', inplace=True)

        file_exists = os.path.exists(self.filename)
        write_header = not file_exists

        try:
            df.to_csv(self.filename, mode='a', header=write_header, index=False)
        except Exception as e:
            print(f"Error writing to CSV for ReqId {reqId}: {e}. Marking for retry.")
            bar_collection[reqId]['status'] = "error_retry"
            bar_collection[reqId]['last_error_code'] = CSV_WRITE_ERROR_CODE
            if 'retry_count' not in bar_collection[reqId]: # Ensure retry_count is initialized
                bar_collection[reqId]['retry_count'] = 0
            return # Keep data for retry attempt

        bar_collection[reqId]['data'] = [] 
        bar_collection[reqId]['status'] = "complete"

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        adv_reject_str = advancedOrderRejectJson if advancedOrderRejectJson else 'None'
        print(f"Error. ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}, Advanced: {adv_reject_str}")
        
        # 1. Check for true connection errors that require a full client reconnect
        if errorCode in RETRYABLE_ERROR_CODES:
            if not self.connection_lost: # Only act if not already in a lost state
                print(f"True connection error detected (Code: {errorCode}). Setting connection_lost = True.")
                self.connection_lost = True
                self.is_connected_event.clear()
                self.next_valid_req_id_ready.clear()
            else:
                print(f"Connection error (Code: {errorCode}) received while already in connection_lost state.")
            return # Important: Exit after handling a true connection error

        # 2. Check for informational codes that should be logged but not disrupt connection state
        if errorCode in INFORMATIONAL_API_CODES:
            print(f"Informational message (Code: {errorCode}). No action taken for connection state.")
            # If it's a "farm is OK" message after a connection_lost, maybe reset connection_lost?
            # Example: if self.connection_lost and errorCode in {2104, 2106, 2158}:
            #    print(f"Farm status OK (Code: {errorCode}) received. Considering connection potentially restored.")
            #    self.connection_lost = False # This is debatable, depends on how strictly you want to manage it
            return # Important: Exit after logging an informational message

        # 3. Handle errors specific to a request (reqId != -1)
        if reqId != -1 and reqId in bar_collection:
            if errorCode in REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES:
                print(f"Request-specific retryable error for ReqId {reqId} (Code: {errorCode}). Marking for retry.")
                bar_collection[reqId]['status'] = "error_retry"
                bar_collection[reqId]['last_error_code'] = errorCode
                if 'retry_count' not in bar_collection[reqId]:
                    bar_collection[reqId]['retry_count'] = 0
            elif errorCode == 200: # No security definition for this request
                 print(f"Error 200 for ReqId {reqId}: No security definition. Marking as failed_permanent.")
                 bar_collection[reqId]['status'] = "failed_permanent"
                 bar_collection[reqId]['last_error_code'] = errorCode
            # Add other specific non-retryable error handling for reqId here
            else: # Unhandled error for a specific request
                print(f"Unhandled error for active ReqId {reqId} (Code: {errorCode}). Marking as failed_permanent.")
                bar_collection[reqId]['status'] = "failed_permanent"
                bar_collection[reqId]['last_error_code'] = errorCode
            return

        # 4. Handle general errors not tied to a request (reqId == -1) and not connection/informational
        if reqId == -1:
            print(f"General system message (Code: {errorCode}). Not a connection error, informational, or request-specific.")
            # Decide if any action is needed for these. Usually just logging is fine.

def load_config(config_path="config.json"):
    global config
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        print(f"Configuration loaded from {config_path}")
    except FileNotFoundError:
        print(f"Error: Configuration file {config_path} not found. Exiting.")
        exit(1)
    except json.JSONDecodeError:
        print(f"Error: Could not decode JSON from {config_path}. Check for syntax errors. Exiting.")
        exit(1)
    except Exception as e:
        print(f"An unexpected error occurred while loading config: {e}. Exiting.")
        exit(1)

def request_historical_data(app, contract_obj, end_date_str, req_id):
    app.reqHistoricalData(reqId=req_id,
                          contract=contract_obj,
                          endDateTime=end_date_str,
                          durationStr="1 W",
                          barSizeSetting="1 min",
                          whatToShow="TRADES",
                          useRTH=1,
                          formatDate=1,
                          keepUpToDate=False,
                          chartOptions=[])

def main():
    global config
    load_config()

    contract_details = config.get("contract", {})
    date_range_cfg = config.get("date_range", {})
    api_cfg = config.get("api_settings", {})
    output_cfg = config.get("output", {})
    tz_cfg = config.get("timezone_overrides", {})

    IBKR_PORT = api_cfg.get("ibkr_port", 7497)
    CLIENT_ID = api_cfg.get("client_id", 1)
    MAX_ACTIVE_REQUESTS = api_cfg.get("max_active_requests", 5)
    INTER_REQUEST_DELAY = api_cfg.get("inter_request_delay_seconds", 2)
    MAX_CONNECT_RETRIES = api_cfg.get("max_connect_retries", 5)
    CONNECT_RETRY_DELAY = api_cfg.get("connect_retry_delay_seconds", 30)
    MAX_REQUEST_RETRIES = api_cfg.get("max_request_retries", 3)
    REQUEST_RETRY_DELAY = api_cfg.get("request_retry_delay_seconds", 10)

    contract = Contract()
    contract.symbol = contract_details.get("symbol", "DEFAULT_SYMBOL")
    contract.secType = contract_details.get("secType", "STK")
    contract.exchange = contract_details.get("exchange", "SMART")
    contract.currency = contract_details.get("currency", "USD")
    primary_exchange_val = contract_details.get("primaryExchange")
    if primary_exchange_val: 
        contract.primaryExchange = primary_exchange_val

    if contract.symbol == "DEFAULT_SYMBOL":
        print("Error: Contract symbol not defined in config.json. Exiting.")
        return

    today = datetime.date.today()
    default_start_year = today.year - 1 if today.month == 1 and today.day == 1 else today.year
    overall_start_date = datetime.date(
        date_range_cfg.get("start_year", default_start_year),
        date_range_cfg.get("start_month", 1),
        date_range_cfg.get("start_day", 1)
    )
    overall_end_date = datetime.date(
        date_range_cfg.get("end_year", today.year),
        date_range_cfg.get("end_month", today.month),
        date_range_cfg.get("end_day", today.day)
    )
    
    filename_template = output_cfg.get("filename_template", "{symbol}_1min_{start_year}_{end_year}_incremental.csv")
    filename = filename_template.format(
        symbol=contract.symbol,
        start_year=overall_start_date.year,
        end_year=overall_end_date.year
    )
    print(f"Data will be saved to: {filename}")

    current_run_start_date = overall_start_date
    if os.path.exists(filename):
        try:
            if os.path.getsize(filename) > 0:
                df_all_dates = pd.read_csv(filename, usecols=['date'])
                if not df_all_dates.empty:
                    last_date_str = df_all_dates['date'].iloc[-1]
                    last_recorded_datetime_utc = pd.to_datetime(last_date_str, utc=True)
                    if pd.notna(last_recorded_datetime_utc):
                        resume_from_date = last_recorded_datetime_utc.date() + datetime.timedelta(days=1)
                        if resume_from_date > current_run_start_date:
                            print(f"Resuming download. Last data in {filename} up to {last_recorded_datetime_utc.date()}.")
                            current_run_start_date = resume_from_date
                            print(f"New effective start date for this run: {current_run_start_date}")
            else:
                print(f"{filename} exists but is empty. Using original start_date: {current_run_start_date}")
        except pd.errors.EmptyDataError:
            print(f"Warning: {filename} is empty (pd.errors.EmptyDataError). Using original start_date: {current_run_start_date}")
        except Exception as e:
            print(f"Warning: Error reading existing {filename} for resume: {e}. Using original start_date: {current_run_start_date}")

    if current_run_start_date > overall_end_date:
        print(f"Effective start date {current_run_start_date} is after overall end date {overall_end_date}. No new data to download.")
        return

    exchange_to_timezone_map = tz_cfg.get("exchange_map", {})
    currency_to_timezone_map = tz_cfg.get("currency_map", {})
    expected_timezone_str = tz_cfg.get("default_fallback", "America/New_York")
    if contract.exchange in exchange_to_timezone_map:
        expected_timezone_str = exchange_to_timezone_map[contract.exchange]
    elif contract.primaryExchange and contract.primaryExchange in exchange_to_timezone_map:
         expected_timezone_str = exchange_to_timezone_map[contract.primaryExchange]
    elif contract.currency in currency_to_timezone_map:
        expected_timezone_str = currency_to_timezone_map[contract.currency]
    print(f"Determined expected timezone for contract {contract.symbol}: {expected_timezone_str}")

    app = IBapi(filename=filename, expected_timezone_str=expected_timezone_str)
    api_thread = None

    connected_successfully = False
    for attempt in range(MAX_CONNECT_RETRIES):
        print(f"Attempting to connect to IBKR (Port: {IBKR_PORT}, ClientID: {CLIENT_ID}) (Attempt {attempt + 1}/{MAX_CONNECT_RETRIES})...")
        app.is_connected_event.clear()
        app.next_valid_req_id_ready.clear()
        if app.isConnected():
            app.disconnect()
            if api_thread and api_thread.is_alive():
                api_thread.join(timeout=5)
        app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
        api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread")
        api_thread.start()
        if app.is_connected_event.wait(timeout=15) and app.next_valid_req_id_ready.wait(timeout=5):
            print("Successfully connected and nextValidId received.")
            connected_successfully = True
            break
        else:
            print("Failed to connect or receive nextValidId within timeout.")
            app.disconnect() 
            if api_thread and api_thread.is_alive():
                api_thread.join(timeout=5)
            if attempt < MAX_CONNECT_RETRIES - 1:
                print(f"Retrying connection in {CONNECT_RETRY_DELAY} seconds...")
                time.sleep(CONNECT_RETRY_DELAY)
            else:
                print("Max connection retries reached. Exiting.")
                return 
    if not connected_successfully: return

    tasks_to_process = []
    temp_date_iterator = overall_end_date 
    while temp_date_iterator >= current_run_start_date:
        tasks_to_process.append(temp_date_iterator)
        temp_date_iterator -= datetime.timedelta(days=7)
    if not tasks_to_process and current_run_start_date <= overall_end_date :
        tasks_to_process.append(overall_end_date)
    print(f"Generated {len(tasks_to_process)} weekly tasks from {current_run_start_date} to {overall_end_date}.")

    while True:
        if app.connection_lost: # This flag is set by the error callback for true connection errors
            print("Connection lost. Attempting to reconnect...")
            app.disconnect() 
            if api_thread and api_thread.is_alive():
                 api_thread.join(timeout=10) 
            reconnected = False
            for rec_attempt in range(MAX_CONNECT_RETRIES):
                print(f"Reconnect attempt {rec_attempt + 1}/{MAX_CONNECT_RETRIES}...")
                app.is_connected_event.clear()
                app.next_valid_req_id_ready.clear()
                app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
                api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread-Reconnect")
                api_thread.start()
                if app.is_connected_event.wait(timeout=15) and app.next_valid_req_id_ready.wait(timeout=5):
                    print("Successfully reconnected.")
                    # app.connection_lost = False # This will be reset by nextValidId
                    reconnected = True
                    break
                else: # Reconnect attempt failed
                    app.disconnect()
                    if api_thread and api_thread.is_alive(): api_thread.join(timeout=10)
                    if rec_attempt < MAX_CONNECT_RETRIES - 1: time.sleep(CONNECT_RETRY_DELAY)
            if not reconnected:
                print("Failed to reconnect after multiple attempts. Exiting processing loop.")
                break 
        
        active_requests_count = sum(1 for req_info in bar_collection.values() if req_info['status'] == "incomplete")
        
        for r_id in list(bar_collection.keys()): 
            if bar_collection[r_id]['status'] == "error_retry":
                if active_requests_count < MAX_ACTIVE_REQUESTS:
                    if bar_collection[r_id]['retry_count'] < MAX_REQUEST_RETRIES:
                        bar_collection[r_id]['retry_count'] += 1
                        error_code = bar_collection[r_id].get('last_error_code', 'N/A')
                        print(f"Retrying request {r_id} (Attempt {bar_collection[r_id]['retry_count']}/{MAX_REQUEST_RETRIES}), Original EndDate: {bar_collection[r_id]['end_date_str_orig']}, LastError: {error_code}")
                        
                        bar_collection[r_id]['status'] = "incomplete" # Mark for processing
                        
                        if error_code == CSV_WRITE_ERROR_CODE:
                            print(f"Attempting to re-write data for ReqId {r_id} due to previous CSV_WRITE_ERROR.")
                            # Data should still be in bar_collection[r_id]['data']
                            # We can directly call historicalDataEnd, but it needs start/end strings which we don't have here.
                            # A simpler approach for CSV retry: try writing the data directly.
                            if bar_collection[r_id]['data']:
                                temp_df_retry = pd.DataFrame(bar_collection[r_id]['data'])
                                if not temp_df_retry.empty and 'date' in temp_df_retry.columns:
                                    temp_df_retry = temp_df_retry.sort_values('date')
                                    temp_df_retry.drop_duplicates(subset=['date'], keep='first', inplace=True)
                                    file_exists_retry = os.path.exists(app.filename)
                                    write_header_retry = not file_exists_retry
                                    try:
                                        temp_df_retry.to_csv(app.filename, mode='a', header=write_header_retry, index=False)
                                        print(f"Successfully re-wrote data for ReqId {r_id}.")
                                        bar_collection[r_id]['data'] = [] # Clear data
                                        bar_collection[r_id]['status'] = "complete"
                                        # active_requests_count doesn't change as it wasn't an API request
                                        continue # Skip re-requesting from API
                                    except Exception as e_csv_retry:
                                        print(f"Still failed to write CSV for ReqId {r_id} on retry: {e_csv_retry}")
                                        # Status remains error_retry, retry_count is up. Next iteration might try again or fail.
                                else: # No data or bad format, should not happen if CSV_WRITE_ERROR was set
                                    print(f"Warning: CSV_WRITE_ERROR for ReqId {r_id} but data is empty/invalid. Re-fetching.")
                                    bar_collection[r_id]['data'] = [] # Clear any potentially bad data
                                    request_historical_data(app, contract, bar_collection[r_id]['end_date_str_orig'], r_id)
                                    active_requests_count += 1
                                    time.sleep(REQUEST_RETRY_DELAY)
                            else: # No data, re-fetch
                                bar_collection[r_id]['data'] = []
                                request_historical_data(app, contract, bar_collection[r_id]['end_date_str_orig'], r_id)
                                active_requests_count += 1
                                time.sleep(REQUEST_RETRY_DELAY)
                        else: # Not a CSV write error, so re-fetch data from API
                            bar_collection[r_id]['data'] = [] # Clear old data
                            request_historical_data(app, contract, bar_collection[r_id]['end_date_str_orig'], r_id)
                            active_requests_count += 1
                            time.sleep(REQUEST_RETRY_DELAY) 
                    else:
                        print(f"Max retries reached for request {r_id}. Marking as failed_permanent.")
                        bar_collection[r_id]['status'] = "failed_permanent"

        active_requests_count = sum(1 for req_info in bar_collection.values() if req_info['status'] == "incomplete")
        while tasks_to_process and active_requests_count < MAX_ACTIVE_REQUESTS:
            date_to_fetch_end = tasks_to_process.pop(0)
            end_date_str_req = date_to_fetch_end.strftime("%Y%m%d") + " 23:59:59"
            current_req_id = app.get_next_req_id()
            print(f"Preparing new request for week ending: {date_to_fetch_end.strftime('%Y-%m-%d')} with req_id: {current_req_id}")
            bar_collection[current_req_id] = {'data': [], 'status': "incomplete", 'end_date_str_orig': end_date_str_req, 'retry_count': 0}
            request_historical_data(app, contract, end_date_str_req, current_req_id)
            active_requests_count += 1
            time.sleep(INTER_REQUEST_DELAY) 
        
        all_tasks_initiated = not tasks_to_process
        all_requests_settled = True
        if not bar_collection and all_tasks_initiated:
            print("All tasks processed or no tasks were generated for this run.")
            break
        for req_status_info in bar_collection.values():
            if req_status_info['status'] in ["incomplete", "error_retry"]:
                all_requests_settled = False
                break
        if all_tasks_initiated and all_requests_settled:
            print("All tasks processed and all requests settled for this run.")
            break
        time.sleep(1)

    if bar_collection:
        completed_count = sum(1 for req in bar_collection.values() if req['status'] == "complete")
        failed_count = sum(1 for req in bar_collection.values() if req['status'] == "failed_permanent")
        print(f"Processing finished. Total initiated requests in this run: {len(bar_collection)}. Completed: {completed_count}. Failed: {failed_count}.")
    else:
        if not all_tasks_initiated:
             print("No requests were processed, and tasks remained. This might indicate an issue.")
        else:
             print("No new requests were needed or processed in this run (e.g., all data already downloaded).")

    print(f"Final data available in {filename}")
    app.disconnect()
    if api_thread and api_thread.is_alive():
        print("Waiting for API thread to terminate...")
        api_thread.join(timeout=10)
        if api_thread.is_alive(): print("Warning: API thread did not terminate gracefully.")

if __name__ == "__main__":
    main()