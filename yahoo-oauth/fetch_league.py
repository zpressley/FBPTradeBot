

import requests
import xmltodict
import json

# Replace with your access token
ACCESS_TOKEN = "SQDugu6buwoWGtR1Afg0UwBbJnPqDex8QmM_L5U080UtYiEngS.IlDTALimoh6vV3oSKq5vSgN7GcAt8oNT2dyu8oh5xYSXzPAMcWMLRXWhyUdHz2YdPAo44AmEV8trLeMiY2F84kRhQzyILRn3zHf.U_wzGbcvZpouCpEyHZi9vJwZ4Y5MGFSuKDXbpK1oCN0jGv3lYzKy3BspzZu7u9RyaL_KA_1tWwsCsD.4H5EQ.MbrqTjvaw9Lc3YCIXlKlQX.S6uY.K0XrGFDgCvD2YEyO_f96yb3P4M.e8Rq6Iteb3rZgynMqYZyP9QHgua5FCg2s5DNORo3wcTmpbtV1a6ZmyRBfljpvHjdIeLmWl0YCVqPwO3eLDqGXJxC8o3DjO5TSve0NO_TBf.SM1T.aRDkzjBt1vm0Pd21UT7Zn1LvHpXGmI24MUVL5rWmPwmqv8H_w4AQ3olBrw6mtGhytPGot3Hhg939osLgl8lr6uWpCy9GaH6OY5A1ScfK2qEl0Zv.St5_fGOXcNAToyH29wVCv2p4pJsNuZJ60aepDGhfeWWncdmckoGyNfVuncvSycr3qcQkMo3ZMnPGomLSmmon8NVfK0gV1vLB_4Xj3SZkJ7p5WV2BL6G5jEOegcPq07xo3tp4ULe_aZwvqkgqvE7kN_AfxXb0NtqrlWKs9lz1gmeYowg6.9QAIbRKcmo_kG6v.Bij9JYY6hnKJv2j1ljGV9xuUDxka84dMqcFnijBz6wtoTdBpP_.0AaydAwijmIuhMg5Dlmcrq5ZWf1jCd12TvpJpHd2l_3hbj9B8MvDOl4OQlCjKW9P5S7omkZPptGmZqpOrEtnItzE_sI2OceniKDRGILhvoRi7wIqK47M3v5C7SfIF7gBD04Ig0r1wofRbpM1WItWMAd1MyQBHTXcqSaZZR9POlotNHnI-"


# Replace with your actual League ID
LEAGUE_ID = "mlb.l.15505"  # Make sure this is correct

# Yahoo Fantasy API Endpoint for League Data
URL = f"https://fantasysports.yahooapis.com/fantasy/v2/league/{LEAGUE_ID}"

# Headers with Authorization
HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/xml"  # Yahoo returns XML by default
}

# Make Request
response = requests.get(URL, headers=HEADERS)

# Print raw XML response for debugging
print(f"Status Code: {response.status_code}")
print("Raw XML Response:")
print(response.text)

# Convert XML to JSON
try:
    parsed_data = xmltodict.parse(response.text)  # Convert XML to OrderedDict
    json_data = json.dumps(parsed_data, indent=4)  # Convert to formatted JSON
    print("\nConverted JSON Response:")
    print(json_data)
except Exception as e:
    print(f"Error converting XML to JSON: {str(e)}")
