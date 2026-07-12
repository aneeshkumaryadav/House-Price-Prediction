import unittest
import pandas as pd
import os
import sys
# Add project root to Python path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.preprocessing import create_preprocessor
class TestPreprocessing(unittest.TestCase):
    def setUp(self):
        df = pd.read_excel("House-Price-Prediction\dataset\HousePricePredictionindore.xlsx")
        self.X = df.drop("Price", axis=1)
        self.preprocessor = create_preprocessor(self.X)
    def test_preprocessor_created(self):
        """Check if preprocessor is created."""
        self.assertIsNotNone(self.preprocessor)
    def test_fit_transform(self):
        """Check if fit_transform works."""
        transformed = self.preprocessor.fit_transform(self.X)
        self.assertEqual(transformed.shape[0], self.X.shape[0])
    def test_transform_new_data(self):
        """Check if new data can be transformed."""
        self.preprocessor.fit(self.X)
        sample = pd.DataFrame({
            "Location": ["Nipania"],
            "Area": [1500],
            "BHK": [3],
            "Floor": [2],
            "Balconies": [2],
            "Property Age": [5],
            "Parking": ["Yes"],
            "Near Market": ["Yes"],
            "Main Road": ["Yes"],
            "Furnishing": ["Semi-Furnished"]
        })
        transformed = self.preprocessor.transform(sample)
        self.assertEqual(transformed.shape[0], 1)
if __name__ == "__main__":
    unittest.main()