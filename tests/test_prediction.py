import unittest
import pandas as pd
import joblib
class TestHousePricePrediction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Load the trained model once before all tests."""
        cls.model = joblib.load("House-Price-Prediction\models\house_price_model.pkl")
    def test_model_loaded(self):
        """Test whether the model is loaded successfully."""
        self.assertIsNotNone(self.model)
    def test_prediction_output_length(self):
        """Test whether exactly one prediction is returned."""
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
        prediction = self.model.predict(sample)
        self.assertEqual(len(prediction), 1)
    def test_prediction_positive(self):
        """Predicted house price should always be positive."""
        sample = pd.DataFrame({
            "Location": ["Vijay Nagar"],
            "Area": [1800],
            "BHK": [3],
            "Floor": [4],
            "Balconies": [2],
            "Property Age": [3],
            "Parking": ["Yes"],
            "Near Market": ["Yes"],
            "Main Road": ["Yes"],
            "Furnishing": ["Fully-Furnished"]
        })
        prediction = self.model.predict(sample)
        self.assertGreater(prediction[0], 0)
    def test_small_house_prediction(self):
        """Prediction for a small house."""
        sample = pd.DataFrame({
            "Location": ["Bengali Square"],
            "Area": [800],
            "BHK": [2],
            "Floor": [1],
            "Balconies": [1],
            "Property Age": [10],
            "Parking": ["No"],
            "Near Market": ["Yes"],
            "Main Road": ["No"],
            "Furnishing": ["Unfurnished"]
        })
        prediction = self.model.predict(sample)
        self.assertGreater(prediction[0], 0)
    def test_large_house_prediction(self):
        """Prediction for a luxury house."""
        sample = pd.DataFrame({
            "Location": ["Scheme No. 140"],
            "Area": [4500],
            "BHK": [5],
            "Floor": [8],
            "Balconies": [4],
            "Property Age": [2],
            "Parking": ["Yes"],
            "Near Market": ["Yes"],
            "Main Road": ["Yes"],
            "Furnishing": ["Fully-Furnished"]
        })
        prediction = self.model.predict(sample)
        self.assertGreater(prediction[0], 0)
    def test_prediction_datatype(self):
        """Prediction should be a numeric value."""
        sample = pd.DataFrame({
            "Location": ["Nipania"],
            "Area": [1600],
            "BHK": [3],
            "Floor": [2],
            "Balconies": [2],
            "Property Age": [4],
            "Parking": ["Yes"],
            "Near Market": ["No"],
            "Main Road": ["Yes"],
            "Furnishing": ["Semi-Furnished"]
        })
        prediction = self.model.predict(sample)
        self.assertIsInstance(prediction[0], (float, int))
    def test_invalid_missing_column(self):
        """Test prediction with a missing feature column."""
        sample = pd.DataFrame({
            "Location": ["Nipania"],
            "Area": [1500],
            "BHK": [3]
        })
        with self.assertRaises(Exception):
            self.model.predict(sample)
    def test_multiple_predictions(self):
        """Test prediction for multiple houses."""
        sample = pd.DataFrame({
            "Location": ["Nipania", "Vijay Nagar"],
            "Area": [1500, 2200],
            "BHK": [3, 4],
            "Floor": [2, 5],
            "Balconies": [2, 3],
            "Property Age": [5, 2],
            "Parking": ["Yes", "Yes"],
            "Near Market": ["Yes", "Yes"],
            "Main Road": ["Yes", "Yes"],
            "Furnishing": ["Semi-Furnished", "Fully-Furnished"]
        })
        prediction = self.model.predict(sample)
        self.assertEqual(len(prediction), 2)
if __name__ == "__main__":
    unittest.main()