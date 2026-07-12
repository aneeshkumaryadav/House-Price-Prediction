from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import OneHotEncoder
def create_preprocessor(X):
    """
    Creates and returns the preprocessing pipeline.
    """
    # Numerical columns
    num_cols = X.select_dtypes(include=["int64", "float64"]).columns.tolist()
    # Categorical columns
    cat_cols = X.select_dtypes(include=["object", "string", "category"]).columns.tolist()
    # Numerical pipeline
    num_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="median"))
    ])
    # Categorical pipeline
    cat_pipeline = Pipeline([
        ("imputer", SimpleImputer(strategy="most_frequent")),
        ("encoder", OneHotEncoder(handle_unknown="ignore"))
    ])
    # Combine both pipelines
    preprocessor = ColumnTransformer([
        ("num", num_pipeline, num_cols),
        ("cat", cat_pipeline, cat_cols)
    ])
    return preprocessor