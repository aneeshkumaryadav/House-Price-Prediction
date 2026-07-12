import pandas as pd
from sklearn.pipeline import Pipeline
from preprocessing import create_preprocessor
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
from evaluation import evaluate_model
from utils import save_model
df = pd.read_excel("House-Price-Prediction\dataset\HousePricePredictionindore.xlsx")
print(df.head())
print(df.info())
print(df.isnull().sum())
print(df.describe())
print(df.columns)
# Target column
X = df.drop("Price", axis=1)
y = df["Price"]
# Find numerical columns
num_cols = X.select_dtypes(include=['int64', 'float64']).columns.tolist()
# Find categorical columns
cat_cols = X.select_dtypes(include=['object', 'string', 'category']).columns.tolist()
print("Numerical Columns:", num_cols)
print("Categorical Columns:", cat_cols)
preprocessor = create_preprocessor(X)
X_train,X_test,y_train,y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)
model = RandomForestRegressor(
    n_estimators=300,
    random_state=42
)
pipeline = Pipeline([
    ("preprocessor",preprocessor),
    ("model",model)
])
pipeline.fit(X_train,y_train)
prediction = pipeline.predict(X_test)
evaluate_model(y_test, prediction)
save_model(pipeline, "House-Price-Prediction\models\house_price_model.pkl")