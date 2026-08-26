#!/bin/bash
# Complete training and testing pipeline for Kandlebot2 Brain

set -e

echo ""
echo "================================================"
echo "KANDLEBOT2 BRAIN - Training & Testing Pipeline"
echo "================================================"
echo ""

# Color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Generate synthetic data
echo -e "${BLUE}Step 1: Generating synthetic training data...${NC}"
python generate_synthetic_data.py
echo ""

# Step 2: Train the model
echo -e "${BLUE}Step 2: Training the model...${NC}"
python -m training.train_model \
  --csv data/synthetic_signals.csv \
  --out models/model.joblib \
  --test-size 0.2 \
  --random-state 42
echo ""

# Step 3: Run unit tests
echo -e "${BLUE}Step 3: Running unit tests...${NC}"
echo "(Make sure Flask server is NOT running for this)"
pytest tests/ -v
echo ""

# Step 4: Instructions for live testing
echo -e "${YELLOW}Step 4: Ready to test with live signals!${NC}"
echo ""
echo -e "${GREEN}To test the trained model with live signals:${NC}"
echo ""
echo "Terminal 1 - Start the Brain server:"
echo "  cd /Users/michael.hejja/Projects/kandlebot2-brain"
echo "  python -m flask run --port 5000"
echo ""
echo "Terminal 2 - Run signal tests:"
echo "  cd /Users/michael.hejja/Projects/kandlebot2-brain"
echo "  python test_signals.py"
echo ""
echo -e "${GREEN}Model training complete!${NC}"
echo ""
echo "Next steps:"
echo "  • Monitor tf_alignment_score impact on decisions"
echo "  • Collect real trade data (next 7 days)"
echo "  • Label wins/losses after 7 days"
echo "  • Retrain on real data for production"
echo ""
