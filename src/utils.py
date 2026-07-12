import os
import joblib
def save_model(model, model_path):
    """
    Save the trained model to the specified path.
    """
    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(model, model_path)
    print(f"Model saved successfully at: {model_path}")
def load_model(model_path):
    """
    Load a trained model from the specified path.
    """
    model = joblib.load(model_path)
    print(f"Model loaded successfully from: {model_path}")
    return model