"""Generate synthetic labeled training data for testing the model pipeline.

Creates 500 realistic signal samples with features matching FEATURE_COLUMNS.
Labels are created using a simple but realistic rule: alignment matters!
"""
import pandas as pd
import numpy as np

np.random.seed(42)
n_samples = 500

# Create synthetic features matching FEATURE_COLUMNS in brain_app/features.py
data = {
    # 1m indicators
    'rsi': np.random.uniform(20, 80, n_samples),
    'ema_9': np.random.uniform(2400, 2600, n_samples),
    'ema_21': np.random.uniform(2400, 2600, n_samples),
    'macd': np.random.uniform(-10, 10, n_samples),
    'macd_signal': np.random.uniform(-10, 10, n_samples),
    'atr': np.random.uniform(5, 20, n_samples),
    'volume': np.random.uniform(500, 2000, n_samples),
    # Multi-TF alignment features
    'tf_alignment_score': np.random.randint(0, 5, n_samples),
    'tf_30m_aligned': np.random.randint(0, 2, n_samples),
    'tf_1h_aligned': np.random.randint(0, 2, n_samples),
    'tf_4h_aligned': np.random.randint(0, 2, n_samples),
    'tf_12h_aligned': np.random.randint(0, 2, n_samples),
}

# Create labels using realistic trading logic
labels = []
for i in range(n_samples):
    alignment_score = data['tf_alignment_score'][i]
    rsi = data['rsi'][i]
    volume = data['volume'][i]
    macd = data['macd'][i]
    macd_signal = data['macd_signal'][i]
    
    # Good signal criteria:
    # 1. Strong multi-TF alignment (score >= 3)
    # 2. RSI in good zone (35-65, not extreme)
    # 3. Good volume (> 1000)
    # 4. MACD positive momentum (macd > macd_signal)
    
    score = 0
    if alignment_score >= 3:
        score += 2
    if 35 <= rsi <= 65:
        score += 1
    if volume > 1000:
        score += 1
    if macd > macd_signal:
        score += 1
    
    # Probabilistically convert score to label
    # score 4-5: 90% win
    # score 3: 70% win
    # score 2: 50% win
    # score <2: 20% win
    
    if score >= 4:
        label = 1 if np.random.random() < 0.90 else 0
    elif score == 3:
        label = 1 if np.random.random() < 0.70 else 0
    elif score == 2:
        label = 1 if np.random.random() < 0.50 else 0
    else:
        label = 1 if np.random.random() < 0.20 else 0
    
    labels.append(label)

data['label'] = labels

df = pd.DataFrame(data)

# Save to CSV
df.to_csv('data/synthetic_signals.csv', index=False)

# Print statistics
print("✓ Generated synthetic training data")
print(f"\nDataset size: {len(df)} samples")
print(f"Features: {len(df.columns) - 1} (excluding label)")
print(f"\nClass distribution:")
print(df['label'].value_counts())
print(f"\nClass balance: {df['label'].mean()*100:.1f}% positive")

print(f"\nFeature statistics:")
print(df.describe().round(2))

print(f"\nSaved to: data/synthetic_signals.csv")
