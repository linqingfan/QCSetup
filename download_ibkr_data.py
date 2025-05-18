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
    2174,  # Warning: You submitted request with date-time attributes without explicit time zone... (Handled by formatting)
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
        self.next_valid_req_id_ready.wait() # Ensure connection and ID are ready
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
                if len(t_parts) == 3: # Timezone info present in bar.date
                    ibkr_timezone_str = t_parts[2]
                    try:
                        ibkr_timezone = pytz.timezone(ibkr_timezone_str)
                        localized_datetime = ibkr_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                    except pytz.exceptions.UnknownTimeZoneError:
                        print(f"Warning: Unknown timezone '{ibkr_timezone_str}' in bar data, using expected: {self.expected_timezone.zone}")
                        localized_datetime = self.expected_timezone.localize(naive_datetime)
                        processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
                else: # No timezone info in bar.date, use the expected timezone for the instrument
                    localized_datetime = self.expected_timezone.localize(naive_datetime)
                    processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error parsing datetime string component '{datetime_str_component}' from '{t_str_original}': {e}. Skipping bar.")
                return
        else: # Only date, no time (daily data or similar)
            try:
                naive_datetime = datetime.datetime.strptime(t_parts[0], '%Y%m%d')
                localized_datetime = self.expected_timezone.localize(naive_datetime) # Assume date is in instrument's timezone
                processed_datetime_utc = localized_datetime.astimezone(pytz.utc)
            except ValueError as e:
                print(f"Error parsing date string component '{t_parts[0]}' from '{t_str_original}': {e}. Skipping bar.")
                return
        
        if processed_datetime_utc is None:
            print(f"Could not process timestamp: {t_str_original}. Skipping bar.")
            return

        if reqId in bar_collection:
            data = {
                'date': processed_datetime_utc, # Store as UTC datetime object
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
            print(f"No data received for ReqId {reqId} (Start: {start}, End: {end}). Marking as complete.")
            bar_collection[reqId]['status'] = "complete"
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
            
        df['date'] = pd.to_datetime(df['date'], utc=True) # Ensure it's datetime if not already
        df = df.sort_values('date')
        df.drop_duplicates(subset=['date'], keep='first', inplace=True)
        
        # Convert UTC datetime back to string format for CSV, consistent with previous output if desired
        # Or keep as datetime objects and let to_csv handle it (usually ISO format)
        # For consistency with potential existing files, converting to "YYYY-MM-DD HH:MM:SS" (implicitly UTC now)
        df['date'] = df['date'].dt.strftime('%Y-%m-%d %H:%M:%S')


        file_exists = os.path.exists(self.filename)
        write_header = not file_exists or os.path.getsize(self.filename) == 0

        try:
            df.to_csv(self.filename, mode='a', header=write_header, index=False)
            print(f"Data for ReqId {reqId} successfully written to {self.filename}")
        except Exception as e:
            print(f"Error writing to CSV for ReqId {reqId}: {e}. Marking for retry.")
            bar_collection[reqId]['status'] = "error_retry"
            bar_collection[reqId]['last_error_code'] = CSV_WRITE_ERROR_CODE
            if 'retry_count' not in bar_collection[reqId]:
                bar_collection[reqId]['retry_count'] = 0
            return # Keep data for retry attempt

        bar_collection[reqId]['data'] = [] 
        bar_collection[reqId]['status'] = "complete"

    def error(self, reqId: int, errorCode: int, errorString: str, advancedOrderRejectJson: str = ""):
        adv_reject_str = advancedOrderRejectJson if advancedOrderRejectJson else 'None'
        log_msg = f"Error. ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}, Advanced: {adv_reject_str}"
        
        if errorCode == 2174: # Specific warning we are addressing
            print(f"Warning (Code 2174 handled by request format): {errorString}")
            return # This is now informational for us as we are sending the TZ

        print(log_msg)
        
        if errorCode in RETRYABLE_ERROR_CODES:
            if not self.connection_lost:
                print(f"True connection error detected (Code: {errorCode}). Setting connection_lost = True.")
                self.connection_lost = True
                self.is_connected_event.clear()
                self.next_valid_req_id_ready.clear()
            else:
                print(f"Connection error (Code: {errorCode}) received while already in connection_lost state.")
            return

        if errorCode in INFORMATIONAL_API_CODES:
            print(f"Informational message (Code: {errorCode}). No action taken for connection state.")
            return

        if reqId != -1 and reqId in bar_collection:
            if errorCode in REQUEST_SPECIFIC_RETRYABLE_ERROR_CODES:
                print(f"Request-specific retryable error for ReqId {reqId} (Code: {errorCode}). Marking for retry.")
                bar_collection[reqId]['status'] = "error_retry"
                bar_collection[reqId]['last_error_code'] = errorCode
                if 'retry_count' not in bar_collection[reqId]:
                    bar_collection[reqId]['retry_count'] = 0
            elif errorCode == 200: # No security definition
                 print(f"Error 200 for ReqId {reqId}: No security definition. Marking as failed_permanent.")
                 bar_collection[reqId]['status'] = "failed_permanent"
                 bar_collection[reqId]['last_error_code'] = errorCode
            else:
                print(f"Unhandled error for active ReqId {reqId} (Code: {errorCode}). Marking as failed_permanent.")
                bar_collection[reqId]['status'] = "failed_permanent"
                bar_collection[reqId]['last_error_code'] = errorCode
            return

        if reqId == -1:
            print(f"General system message (Code: {errorCode}). Not a connection error, informational, or request-specific.")

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

def request_historical_data(app, contract_obj, end_date_time_str_with_tz, req_id):
    """
    Requests historical data.
    end_date_time_str_with_tz: Must be in format "YYYYMMDD HH:MM:SS TZ_Name" e.g. "20231231 23:59:59 America/New_York"
    """
    print(f"Requesting historical data for ReqId: {req_id}, Contract: {contract_obj.symbol}, EndDateTime: '{end_date_time_str_with_tz}'")
    app.reqHistoricalData(reqId=req_id,
                          contract=contract_obj,
                          endDateTime=end_date_time_str_with_tz, # Use the string with timezone
                          durationStr="1 W",
                          barSizeSetting="1 min",
                          whatToShow="TRADES",
                          useRTH=1,       # Get data for regular trading hours
                          formatDate=1,   # Return as yyyyMMdd HH:mm:ss
                          keepUpToDate=False,
                          chartOptions=[]) # chartOptions should be a list of TagValue items

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
    INTER_REQUEST_DELAY = api_cfg.get("inter_request_delay_seconds", 2) # Increased default slightly
    MAX_CONNECT_RETRIES = api_cfg.get("max_connect_retries", 5)
    CONNECT_RETRY_DELAY = api_cfg.get("connect_retry_delay_seconds", 30)
    MAX_REQUEST_RETRIES = api_cfg.get("max_request_retries", 3)
    REQUEST_RETRY_DELAY = api_cfg.get("request_retry_delay_seconds", 15) # Increased default slightly

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
    
    filename_template = output_cfg.get("filename_template", "{symbol}_1min_{start_year}_{end_year}_utc.csv")
    filename = filename_template.format(
        symbol=contract.symbol,
        start_year=overall_start_date.year,
        end_year=overall_end_date.year
    )
    print(f"Data will be saved to: {filename} (timestamps in UTC)")

    current_run_start_date = overall_start_date
    if os.path.exists(filename):
        try:
            if os.path.getsize(filename) > 0: # Check if file is not empty
                # Assuming CSV 'date' column is 'YYYY-MM-DD HH:MM:SS' and represents UTC
                df_all_dates = pd.read_csv(filename, usecols=['date'], parse_dates=['date'], infer_datetime_format=True)
                if not df_all_dates.empty:
                    # Ensure dates are timezone-aware (UTC) before finding max
                    df_all_dates['date'] = pd.to_datetime(df_all_dates['date'], utc=True)
                    last_recorded_datetime_utc = df_all_dates['date'].max()
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
        except KeyError:
             print(f"Warning: 'date' column not found in {filename}. Cannot resume. Using original start_date: {current_run_start_date}")
        except Exception as e:
            print(f"Warning: Error reading existing {filename} for resume: {e}. Using original start_date: {current_run_start_date}")

    if current_run_start_date > overall_end_date:
        print(f"Effective start date {current_run_start_date} is after overall end date {overall_end_date}. No new data to download.")
        return

    exchange_to_timezone_map = tz_cfg.get("exchange_map", {})
    currency_to_timezone_map = tz_cfg.get("currency_map", {})
    expected_timezone_str = tz_cfg.get("default_fallback", "America/New_York") # Default if not found
    
    # Determine instrument's primary timezone
    if contract.exchange and contract.exchange.upper() in exchange_to_timezone_map:
        expected_timezone_str = exchange_to_timezone_map[contract.exchange.upper()]
    elif contract.primaryExchange and contract.primaryExchange.upper() in exchange_to_timezone_map:
         expected_timezone_str = exchange_to_timezone_map[contract.primaryExchange.upper()]
    elif contract.currency and contract.currency.upper() in currency_to_timezone_map:
        expected_timezone_str = currency_to_timezone_map[contract.currency.upper()]
    print(f"Determined expected timezone for contract {contract.symbol}: {expected_timezone_str}")

    app = IBapi(filename=filename, expected_timezone_str=expected_timezone_str)
    api_thread = None
    api_request_timezone_str = app.expected_timezone.zone # Get the Olson timezone name string

    connected_successfully = False
    for attempt in range(MAX_CONNECT_RETRIES):
        print(f"Attempting to connect to IBKR (Port: {IBKR_PORT}, ClientID: {CLIENT_ID}) (Attempt {attempt + 1}/{MAX_CONNECT_RETRIES})...")
        app.is_connected_event.clear()
        app.next_valid_req_id_ready.clear()
        app.connection_lost = False # Reset before attempting connection
        if app.isConnected():
            print("Disconnecting existing stale connection...")
            app.disconnect()
            if api_thread and api_thread.is_alive():
                api_thread.join(timeout=5)
        
        app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
        api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread")
        api_thread.start()

        if app.is_connected_event.wait(timeout=15) and app.next_valid_req_id_ready.wait(timeout=10): # Increased nextValidId timeout
            print("Successfully connected and nextValidId received.")
            connected_successfully = True
            break
        else:
            print("Failed to connect or receive nextValidId within timeout.")
            if app.isConnected(): app.disconnect() 
            if api_thread and api_thread.is_alive():
                api_thread.join(timeout=5)
            if attempt < MAX_CONNECT_RETRIES - 1:
                print(f"Retrying connection in {CONNECT_RETRY_DELAY} seconds...")
                time.sleep(CONNECT_RETRY_DELAY)
            else:
                print("Max connection retries reached. Exiting.")
                return 
    if not connected_successfully: return

    tasks_to_process = [] # List of datetime.date objects (end date of the week to fetch)
    temp_date_iterator = overall_end_date 
    while temp_date_iterator >= current_run_start_date:
        tasks_to_process.append(temp_date_iterator)
        temp_date_iterator -= datetime.timedelta(days=7) # Request 1 week at a time
    # tasks_to_process will be in reverse chronological order, pop(0) will get earliest date first
    tasks_to_process.reverse() 

    if not tasks_to_process and current_run_start_date <= overall_end_date :
        # If start and end are within the same initial 7-day block
        tasks_to_process.append(overall_end_date)
    print(f"Generated {len(tasks_to_process)} weekly tasks from {current_run_start_date} to {overall_end_date}.")

    while True:
        if app.connection_lost:
            print("Connection lost. Attempting to reconnect...")
            if app.isConnected(): app.disconnect() 
            if api_thread and api_thread.is_alive():
                 api_thread.join(timeout=10) 
            
            reconnected = False
            for rec_attempt in range(MAX_CONNECT_RETRIES):
                print(f"Reconnect attempt {rec_attempt + 1}/{MAX_CONNECT_RETRIES}...")
                app.is_connected_event.clear()
                app.next_valid_req_id_ready.clear()
                app.connection_lost = False # Reset before attempting connection
                app.connect('127.0.0.1', IBKR_PORT, clientId=CLIENT_ID)
                api_thread = threading.Thread(target=app.run, daemon=True, name="IBAPIThread-Reconnect")
                api_thread.start()
                if app.is_connected_event.wait(timeout=15) and app.next_valid_req_id_ready.wait(timeout=10):
                    print("Successfully reconnected.")
                    reconnected = True
                    break
                else: 
                    print("Reconnect attempt failed.")
                    if app.isConnected(): app.disconnect()
                    if api_thread and api_thread.is_alive(): api_thread.join(timeout=10)
                    if rec_attempt < MAX_CONNECT_RETRIES - 1: time.sleep(CONNECT_RETRY_DELAY)
            if not reconnected:
                print("Failed to reconnect after multiple attempts. Exiting processing loop.")
                break 
        
        active_requests_count = sum(1 for req_info in bar_collection.values() if req_info['status'] == "incomplete")
        
        # Process retries first
        for r_id in list(bar_collection.keys()): 
            if bar_collection[r_id]['status'] == "error_retry":
                if active_requests_count < MAX_ACTIVE_REQUESTS:
                    if bar_collection[r_id]['retry_count'] < MAX_REQUEST_RETRIES:
                        bar_collection[r_id]['retry_count'] += 1
                        error_code = bar_collection[r_id].get('last_error_code', 'N/A')
                        original_end_date_obj = bar_collection[r_id]['end_date_obj_for_req'] # Get the date object

                        print(f"Retrying request {r_id} (Attempt {bar_collection[r_id]['retry_count']}/{MAX_REQUEST_RETRIES}), Original EndDate (obj): {original_end_date_obj.strftime('%Y-%m-%d')}, LastError: {error_code}")
                        
                        bar_collection[r_id]['status'] = "incomplete"
                        
                        if error_code == CSV_WRITE_ERROR_CODE:
                            print(f"Attempting to re-write data for ReqId {r_id} due to previous CSV_WRITE_ERROR.")
                            if bar_collection[r_id]['data']:
                                temp_df_retry = pd.DataFrame(bar_collection[r_id]['data'])
                                if not temp_df_retry.empty and 'date' in temp_df_retry.columns:
                                    temp_df_retry['date'] = pd.to_datetime(temp_df_retry['date'], utc=True)
                                    temp_df_retry = temp_df_retry.sort_values('date')
                                    temp_df_retry.drop_duplicates(subset=['date'], keep='first', inplace=True)
                                    temp_df_retry['date'] = temp_df_retry['date'].dt.strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    file_exists_retry = os.path.exists(app.filename)
                                    write_header_retry = not file_exists_retry or os.path.getsize(app.filename) == 0
                                    try:
                                        temp_df_retry.to_csv(app.filename, mode='a', header=write_header_retry, index=False)
                                        print(f"Successfully re-wrote data for ReqId {r_id}.")
                                        bar_collection[r_id]['data'] = [] 
                                        bar_collection[r_id]['status'] = "complete"
                                        continue # Skip re-requesting from API for this specific retry
                                    except Exception as e_csv_retry:
                                        print(f"Still failed to write CSV for ReqId {r_id} on retry: {e_csv_retry}. Will re-fetch data.")
                                        # Fall through to re-fetch data
                                else: 
                                    print(f"Warning: CSV_WRITE_ERROR for ReqId {r_id} but data is empty/invalid. Re-fetching.")
                            else: # No data to retry writing, must re-fetch
                                 print(f"No data to retry writing for CSV_WRITE_ERROR on ReqId {r_id}. Re-fetching.")
                            
                            # Common path for re-fetching if CSV retry fails or data was bad/missing
                            bar_collection[r_id]['data'] = [] 
                            end_date_str_for_api_retry = original_end_date_obj.strftime("%Y%m%d") + f" 23:59:59 {api_request_timezone_str}"
                            request_historical_data(app, contract, end_date_str_for_api_retry, r_id)
                            active_requests_count += 1
                            time.sleep(REQUEST_RETRY_DELAY)

                        else: # Not a CSV write error, so re-fetch data from API
                            bar_collection[r_id]['data'] = [] 
                            end_date_str_for_api_retry = original_end_date_obj.strftime("%Y%m%d") + f" 23:59:59 {api_request_timezone_str}"
                            request_historical_data(app, contract, end_date_str_for_api_retry, r_id)
                            active_requests_count += 1
                            time.sleep(REQUEST_RETRY_DELAY) 
                    else:
                        print(f"Max retries reached for request {r_id} associated with end date {bar_collection[r_id]['end_date_obj_for_req'].strftime('%Y-%m-%d')}. Marking as failed_permanent.")
                        bar_collection[r_id]['status'] = "failed_permanent"
                else:
                    break # Max active requests reached, process retries later

        active_requests_count = sum(1 for req_info in bar_collection.values() if req_info['status'] == "incomplete")
        
        # Launch new tasks if capacity allows
        while tasks_to_process and active_requests_count < MAX_ACTIVE_REQUESTS:
            date_to_fetch_end = tasks_to_process.pop(0) # This is a datetime.date object
            
            # Construct endDateTime string with timezone for the API
            end_date_str_req_for_api = date_to_fetch_end.strftime("%Y%m%d") + f" 23:59:59 {api_request_timezone_str}"
            
            current_req_id = app.get_next_req_id()
            print(f"Preparing new request for week ending: {date_to_fetch_end.strftime('%Y-%m-%d')} with req_id: {current_req_id}, API endDateTime: '{end_date_str_req_for_api}'")
            
            bar_collection[current_req_id] = {
                'data': [], 
                'status': "incomplete", 
                'end_date_obj_for_req': date_to_fetch_end, # Store the date object
                'retry_count': 0
            }
            request_historical_data(app, contract, end_date_str_req_for_api, current_req_id)
            active_requests_count += 1
            time.sleep(INTER_REQUEST_DELAY) 
        
        all_tasks_initiated = not tasks_to_process
        all_requests_settled = True
        if not bar_collection and all_tasks_initiated: # No requests made and no tasks left
            print("All tasks processed or no tasks were generated for this run.")
            break
        
        pending_requests = False
        for req_id_check, req_status_info in bar_collection.items():
            if req_status_info['status'] in ["incomplete", "error_retry"]:
                all_requests_settled = False
                pending_requests = True
                # print(f"Debug: ReqId {req_id_check} is still {req_status_info['status']}") # Optional debug
                break
        
        if all_tasks_initiated and all_requests_settled:
            print("All tasks processed and all requests settled (completed or permanently failed) for this run.")
            break
        
        if not pending_requests and not tasks_to_process: # Should be caught by above, but as a safeguard
            print("No pending requests and no tasks to process. Exiting loop.")
            break

        time.sleep(1) # Main loop sleep

    # Final summary
    if bar_collection:
        completed_count = sum(1 for req in bar_collection.values() if req['status'] == "complete")
        failed_count = sum(1 for req in bar_collection.values() if req['status'] == "failed_permanent")
        print(f"Processing finished. Total initiated request slots in this run: {len(bar_collection)}.")
        print(f"  Successfully completed and data saved: {completed_count}")
        print(f"  Permanently failed: {failed_count}")
        if failed_count > 0:
            print("  Failed request details:")
            for r_id, info in bar_collection.items():
                if info['status'] == 'failed_permanent':
                    print(f"    ReqId: {r_id}, End Date: {info['end_date_obj_for_req'].strftime('%Y-%m-%d')}, Last Error: {info.get('last_error_code', 'N/A')}")
    else:
        if not all_tasks_initiated: # Should ideally not happen if tasks were generated
             print("No requests were processed, and tasks remained. This might indicate an issue.")
        else:
             print("No new requests were needed or processed in this run (e.g., all data already downloaded or no date range).")

    print(f"Final data available in {filename}")
    if app.isConnected():
        print("Disconnecting from IBKR...")
        app.disconnect()
    if api_thread and api_thread.is_alive():
        print("Waiting for API thread to terminate...")
        api_thread.join(timeout=10)
        if api_thread.is_alive(): print("Warning: API thread did not terminate gracefully.")
    print("Script finished.")

if __name__ == "__main__":
    main()
