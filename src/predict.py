import pandas as pd
import joblib
# Load the trained model
model = joblib.load("House-Price-Prediction\models\house_price_model.pkl")
# Sample house details
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
# Predict the price
predicted_price = model.predict(sample)
print("Predicted House Price:", round(predicted_price[0], 2), "Lakhs")