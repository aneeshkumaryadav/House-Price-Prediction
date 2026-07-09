from flask import Flask

app = Flask(__name__)

@app.route("/")
@app.route("/home")
def hello_world():
    return "<h1>Welcome to the House Price Prediction System</h1>"

if __name__ == "__main__":
    app.run(debug=True)