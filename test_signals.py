"""Test the trained model with sample signals.

This script simulates real signal analysis and shows how the model performs.
"""
import requests
import json
import time

BRAIN_URL = "http://localhost:5000"

# Test signals with different characteristics
TEST_SIGNALS = [
    {
        "name": "A-Tier SWING (Perfect alignment + high momentum)",
        "payload": {
            "symbol": "ETH",
            "signal_type": "long",
            "indicators": {
                "close": 2501.0,
                "rsi": 70.0,  # Strong momentum (20 from neutral)
                "ema_9": 2500.5,
                "ema_21": 2499.0,
                "ema_200": 2490.0,
                "macd": 2.0,  # Decent but not extreme MACD
                "macd_signal": 1.0,
                "atr": 12.0,
                "volume": 1500.0,
                # NEW: Reversal signals from MFI detector
                "price_trend": 45.0,      # Price trending up
                "mfi_trend": 40.0,        # MFI also trending up (CONFIRMING)
                "reversal_signal": "BUY", # Reversal detector agrees with LONG signal
                "reversal_confidence": 72.0,  # High confidence in reversal
            }
        }
    },
    {
        "name": "B-Tier SWING (Neutral momentum, perfect alignment)",
        "payload": {
            "symbol": "ETH",
            "signal_type": "long",
            "indicators": {
                "close": 2501.5,
                "rsi": 52.0,  # Neutral RSI (only 2 from neutral)
                "ema_9": 2500.0,
                "ema_21": 2499.5,
                "ema_200": 2488.0,
                "macd": 1.0,  # Weak MACD
                "macd_signal": 0.8,
                "atr": 10.0,
                "volume": 1200.0,
                # Weak reversal signal
                "price_trend": 20.0,      # Weak uptrend
                "mfi_trend": 15.0,        # Weak MFI uptrend
                "reversal_signal": None,  # No strong reversal detected
                "reversal_confidence": 35.0,  # Low confidence
            }
        }
    },
    {
        "name": "A-Tier SCALP (EXTREME RSI + strong MACD)",
        "payload": {
            "symbol": "BTC",
            "signal_type": "long",
            "indicators": {
                "close": 59800.0,
                "rsi": 11.0,  # Very extreme oversold (39 from neutral)
                "ema_9": 59750.0,
                "ema_21": 59500.0,
                "ema_200": 59000.0,
                "macd": 5.0,  # Strong bounce momentum
                "macd_signal": 0.5,
                "atr": 200.0,
                "volume": 1200.0,
                # EXTREME reversal signal
                "price_trend": -50.0,     # Price was falling hard
                "mfi_trend": 65.0,        # MFI bouncing up HARD (EXTREME DIVERGENCE)
                "reversal_signal": "BUY", # Classic bottom reversal
                "reversal_confidence": 95.0,  # Very high confidence bottom
            }
        }
    },
    {
        "name": "A-Tier TREND_START (Perfect + strong MACD + trend)",
        "payload": {
            "symbol": "ETH",
            "signal_type": "long",
            "indicators": {
                "close": 2502.0,
                "rsi": 58.0,  # Moderate momentum (8 from neutral)
                "ema_9": 2501.5,
                "ema_21": 2500.0,
                "ema_200": 2470.0,  # Huge gap = strong trend
                "macd": 8.0,  # Very strong MACD
                "macd_signal": 2.0,
                "atr": 15.0,
                "volume": 2000.0,
                # Strong confirming reversal
                "price_trend": 70.0,      # Strong uptrend in price
                "mfi_trend": 68.0,        # Strong uptrend in MFI (PERFECT CONFIRMATION)
                "reversal_signal": "BUY", # Reversal detector agrees strongly
                "reversal_confidence": 88.0,  # High confidence in new trend
            }
        }
    },
]

def test_signal(test_case):
    """Send a signal and report results."""
    print(f"\n{'='*70}")
    print(f"Testing: {test_case['name']}")
    print(f"{'='*70}")
    
    try:
        response = requests.post(
            f"{BRAIN_URL}/analyze",
            json=test_case['payload'],
            timeout=5
        )
        
        if response.status_code == 200:
            result = response.json()
            
            symbol = result.get('symbol')
            signal_type = result.get('signal_type')
            decision = result.get('decision')
            confidence = result.get('confidence')
            model_used = result.get('model_used')
            tf_score = result.get('tf_alignment_score', 'N/A')
            tf_details = result.get('tf_alignment_details', 'N/A')
            
            # NEW: Entry guidance
            entry = result.get('entry_guidance', {})
            entry_rec = entry.get('entry_recommendation', 'N/A')
            entry_price = entry.get('entry_price', 0)
            entry_reason = entry.get('entry_reason', '')
            sl = entry.get('stop_loss', 0)
            tp_cons = entry.get('take_profit_conservative', 0)
            tp_agg = entry.get('take_profit_aggressive', 0)
            rr = entry.get('risk_reward_ratio', 0)
            
            # NEW: Trade classification
            trade = result.get('trade_classification', {})
            trade_type = trade.get('trade_type', 'N/A')
            grade = trade.get('grade', 'N/A')
            hold_time = trade.get('hold_time_estimate', 'N/A')
            risk_level = trade.get('risk_level', 'N/A')
            characteristics = trade.get('characteristics', [])
            
            # NEW: Reversal analysis
            reversal = result.get('reversal_analysis', {})
            reversal_signal = reversal.get('reversal_signal')
            reversal_conf = reversal.get('reversal_confidence', 0)
            alignment_type = reversal.get('alignment_type', 'none')
            reversal_boost = reversal.get('reversal_boost', 0)
            reversal_reasoning = reversal.get('reasoning', '')
            price_trend = reversal.get('price_trend', 0)
            mfi_trend = reversal.get('mfi_trend', 0)
            
            print(f"Symbol: {symbol}")
            print(f"Signal Type: {signal_type}")
            print(f"Decision: {decision.upper()}")
            print(f"Confidence: {confidence:.2%}")
            print(f"Model: {model_used}")
            print(f"TF Alignment Score: {tf_score}/4")
            
            # Display reversal analysis
            print(f"\n🔄 REVERSAL ANALYSIS:")
            print(f"  Type: {alignment_type}")
            if reversal_signal:
                print(f"  Signal: {reversal_signal} (Confidence: {reversal_conf}%)")
                print(f"  Price Trend: {price_trend:.0f} | MFI Trend: {mfi_trend:.0f}")
                print(f"  Confidence Adjustment: {reversal_boost:+.1f}%")
                print(f"  Reasoning: {reversal_reasoning}")
            else:
                print(f"  Signal: None detected")
            
            # Display entry guidance
            print(f"\n📍 ENTRY GUIDANCE:")
            print(f"  Recommendation: {entry_rec}")
            print(f"  Entry Price: {entry_price:.2f}")
            print(f"  Reason: {entry_reason}")
            print(f"  Stop Loss: {sl:.2f}")
            print(f"  Take Profit (Conservative): {tp_cons:.2f}")
            print(f"  Take Profit (Aggressive): {tp_agg:.2f}")
            print(f"  Risk/Reward Ratio: {rr:.2f}:1")
            
            # Display trade classification
            print(f"\n🎯 TRADE CLASSIFICATION:")
            print(f"  Type: {trade_type} ({grade}-Tier)")
            print(f"  Hold Time: {hold_time}")
            print(f"  Risk Level: {risk_level}")
            print(f"  Characteristics:")
            for char in characteristics:
                print(f"    • {char}")
            
            return {
                "name": test_case['name'],
                "decision": decision,
                "confidence": confidence,
                "tf_score": tf_score,
                "trade_type": trade_type,
                "grade": grade,
            }
        else:
            print(f"ERROR: HTTP {response.status_code}")
            print(response.text)
            return None
            
    except requests.exceptions.ConnectionError:
        print("ERROR: Could not connect to Brain server at http://localhost:5000")
        print("Make sure the Flask server is running: python -m flask run --port 5000")
        return None
    except Exception as e:
        print(f"ERROR: {e}")
        return None


def main():
    print("\n" + "="*70)
    print("KANDLEBOT2 BRAIN - Signal Analysis Test Suite")
    print("="*70)
    
    # Check health first
    try:
        health = requests.get(f"{BRAIN_URL}/health", timeout=5).json()
        print(f"\n✓ Brain health: {health['status']}")
        print(f"  Model loaded: {health['model_loaded']}")
    except:
        print("\n✗ Brain server not responding!")
        print("  Start it with: cd kandlebot2-brain && python -m flask run --port 5000")
        return
    
    # Run all test signals
    results = []
    for i, test_case in enumerate(TEST_SIGNALS, 1):
        result = test_signal(test_case)
        if result:
            results.append(result)
        time.sleep(0.5)  # Small delay between requests
    
    # Summary
    print(f"\n\n{'='*70}")
    print("TEST SUMMARY")
    print(f"{'='*70}")
    
    if results:
        accepts = sum(1 for r in results if r['decision'] == 'accept')
        rejects = sum(1 for r in results if r['decision'] == 'reject')
        avg_confidence = sum(r['confidence'] for r in results) / len(results)
        
        print(f"\nTests Run: {len(results)}")
        print(f"Accepted: {accepts} ({accepts/len(results)*100:.0f}%)")
        print(f"Rejected: {rejects} ({rejects/len(results)*100:.0f}%)")
        print(f"Average Confidence: {avg_confidence:.2%}")
        
        print(f"\nDetailed Results:")
        for r in results:
            status = "✓ ACCEPT" if r['decision'] == 'accept' else "✗ REJECT"
            trade_info = f"{r.get('trade_type', 'N/A')} ({r.get('grade', 'N/A')})"
            print(f"  {status} | {r['name'][:35]:35s} | Conf: {r['confidence']:.0%} | Trade: {trade_info}")
    else:
        print("No results - check that Brain server is running")
    
    print(f"\n{'='*70}\n")

if __name__ == "__main__":
    main()
