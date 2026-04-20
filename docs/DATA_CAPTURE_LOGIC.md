every market data update message should contain a local timestamp in capture process.
=============================

our setup is as follows: 

we have an ec2 instance that is colocated; it's in the same aws region & AZ as the exchange. 

every message contains 2 timestamps: 

- exchange timestamp: stamped by the exchange at event time 
- local timestamp: stamped by us at capture time 

we want to use this market data to run backtest simulations with. 

let's start with what our setup would look like in live trading (in production), then we'll build our backtest (historical, i.e. "hist") setup in a way that emulates the prod setup as well as possible. 

in prod, the flow would be: 

1. the md receiver receives an md (market data) update from the exchange. it parses the md update, and forwards it to the stgy
	-> here, the md receiver and stgy would ideally be different processes running on the same box, but for simplicity let's assume they're the same process, as this process topography on the same box does not matter in hist  
2. stgy receives the md update, does calculations, sends new orders or cxls if needed to the exchange
	-> ideally stgy would send an update to an OGW (order gateway), and OGW does all the network-based tasks, however again let's assume these are the same process for simplicity. 
3. exchange receives the request if any (order or cxl), matches it or places it on the book, then sends an ack back to the stgy
4. stgy receives the ack (asynchonously), updates internal state

the primary trading loop is driven by the market data updates.

to the data flow is: 

exchange -[md update]-> stgy -[request (order/cxl)]-> exchange -[acks/fills]-> stgy 

important point here is this: we have to emulate the latencies in between these hops somehow. 

we already have the timestamps for the first hop. the capture process is identical to the strategy in terms of how it receives the md updates. local timestamp is when we received the msg, and the exchange timestamp gives us the timestamp of when the even happened at the exchange side. 

so, from the strategy's point of view, local timestamp is when the events happen. so the strategy internally should set a variable saying "this is the local timestamp now", every time it hears a new md update.  

so when we want to send an order back to the exchange, which is the main part we are trying to emulate, we have to add some predicted latency to this path. 

let's go through an example: 

1. exchange sends md update 1: 

	> exch_ts = 100ms, local_ts = 110ms  (i.e. stgy received it 10ms after the exch ts)
	> stgy now has internal clock set to 110ms internally
	
2. stgy sends an order in this tick. 

	> local_ts = 110ms
	> the last exchange -> stgy latency was 10ms (110ms-100ms). we are predicting that the latency for the next order we send will remain the same as the last tick (which is an OK assumption, as exchange & network latencies are highly autocorrelated)  
	> so we have to have a system in place that will "land" our order at exch_ts = 120ms 
	
this is crucial. this means that the exchange simulator has to: 
	- put our order with the "estimated exchange timestamp" in a heap with all other md updates, keyed by exchange timestamp  
	- "land" our order at the right expected time in the future
	
once the order lands, the exchange sim component can send an ack immediately back to the stgy, or we can also delay the ack, whatever makes more sense (though ack times are much less important compared to getting the actual landing latency part correct)