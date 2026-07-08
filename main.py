from flask import Flask

app = Flask(__name__)

@app.route("/")
@app.route("/home")
@app.route("/Home")
def hello_world():
    return "<h1>House Price Prediction System</h1>"

if __name__ == "__main__":
    app.run(debug=True)