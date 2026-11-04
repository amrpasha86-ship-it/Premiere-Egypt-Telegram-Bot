import requests
import re
import json
import os
import time
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

BASE = "https://mifcentral-api.premiere-cinemas.com"
SITE = "https://www.premiere-cinemas.com"
JS_URL = SITE + "/_next/static/chunks/6212-64483c6c764db7ef.js"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

STATE_FILE = "premiere_state.json"

KEY = bytes.fromhex(
    "e27202c6bea33dfc037280323f14f6bba8abc3af76410e6a56f9648c64aad070"
)
IV = bytes.fromhex("7d020540333b085180529e5a721f44af")

session = requests.Session()


def send_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = session.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=30
    )
    response.raise_for_status()
    if not response.json().get("ok"):
        raise Exception("Telegram error")


def get_headers():
    response = session.get(
        JS_URL,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/javascript,*/*;q=0.1",
            "Referer": SITE + "/"
        },
        timeout=30
    )
    response.raise_for_status()

    match = re.search(r'Authorization:"([^"]+)"', response.text)
    if not match:
        raise Exception("Authorization token not found")

    return {
        "Authorization": match.group(1),
        "x-web-security": "true",
        "x-cinema-source": "0",
        "x-cinema-destination": "0",
        "Origin": SITE,
        "Referer": SITE + "/"
    }


def decrypt_response(data):
    encrypted = bytes.fromhex(data["encryptedData"])
    raw = AES.new(KEY, AES.MODE_CBC, IV).decrypt(encrypted)
    decoded = unpad(raw, AES.block_size).decode("utf-8")
    return json.loads(decoded)


def get_movies(headers):
    url = BASE + "/api/v1/hybrid/get-now-showing-movies"

    response = session.get(
        url,
        params={"lang_id": 1, "is_show_trailers": "Y"},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    result = decrypt_response(response.json())
    return result["Records"]["data"]


def get_showtimes(headers, movie, show_date):
    movie_id = movie["movie_unique_identifier"]

    url = (
        BASE
        + "/api/v1/hybrid/get-movie-show-times/"
        + str(movie_id)
    )

    response = session.get(
        url,
        params={"fromDate": show_date, "lang_id": 1},
        headers=headers,
        timeout=30
    )
    response.raise_for_status()

    result = decrypt_response(response.json())
    shows = {}

    for cinema in result.get("Records", []):
        for schedule in cinema.get("cinemaShowTimes", []):
            for show in schedule.get("movieDetailsData", []):

                show_date_value = (
                    show.get("ss_start_date") or show_date
                )
                show_time = show.get("ss_start_time", "")

                unique_key = "|".join([
                    str(movie_id),
                    str(cinema.get("cinema_id", "")),
                    str(show.get("screen_id", "")),
                    str(show.get("ss_id", "")),
                    str(show_date_value),
                    str(show_time),
                    str(show.get("movie_experience_id", "")),
                    str(show.get("mf_id", ""))
                ])

                shows[unique_key] = {
                    "movie": movie.get("movie_title", ""),
                    "cinema": cinema.get("cinema_name", ""),
                    "experience": show.get("movie_experience", ""),
                    "format": show.get("mf_name", ""),
                    "screen": show.get("screen_name", ""),
                    "date": show_date_value,
                    "time": show_time
                }

    return shows


def load_state():
    if not os.path.exists(STATE_FILE):
        return None

    with open(STATE_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2)


def scan():
    print("Connecting to Premiere...")

    headers = get_headers()
    movies = get_movies(headers)

    old_state = load_state()

    current_movies = {}
    current_shows = {}

    for movie in movies:
        movie_id = str(movie["movie_unique_identifier"])
        current_movies[movie_id] = movie.get("movie_title", "")

        dates = [
            date.strip()
            for date in movie.get("show_dates", "").split(",")
            if date.strip()
        ]

        for show_date in dates:
            try:
                found = get_showtimes(headers, movie, show_date)
                current_shows.update(found)
                time.sleep(1)

            except Exception as error:
                print(
                    "Showtime error:",
                    movie.get("movie_title"),
                    show_date,
                    error
                )
                raise

    new_movies = []
    new_shows = []

    if old_state is None:
        print("First scan: saving initial data.")
        send_message(
            "Premiere Bot initialized successfully.\n"
            f"Movies: {len(current_movies)}\n"
            f"Showtimes: {len(current_shows)}\n"
            "Future changes will be reported."
        )

    else:
        old_movies = old_state.get("movies", {})
        old_shows = old_state.get("shows", {})

        new_movies = [
            title
            for movie_id, title in current_movies.items()
            if movie_id not in old_movies
        ]

        new_shows = [
            show
            for key, show in current_shows.items()
            if key not in old_shows
        ]

        for title in new_movies:
            send_message("NEW MOVIE\nMovie: " + title)
            time.sleep(1)

        for show in new_shows:
            message = (
                "NEW SHOWTIME\n"
                f"Movie: {show['movie']}\n"
                f"Cinema: {show['cinema']}\n"
                f"Experience: {show['experience']}\n"
                f"Format: {show['format']}\n"
                f"Screen: {show['screen']}\n"
                f"Date: {show['date']}\n"
                f"Time: {show['time']}"
            )
            send_message(message)
            time.sleep(1)

    save_state({
        "movies": current_movies,
        "shows": current_shows
    })

    print("Movies:", len(current_movies))
    print("Showtimes:", len(current_shows))
    print("New movies:", len(new_movies))
    print("New showtimes:", len(new_shows))
    print("Scan completed.")


if __name__ == "__main__":
    scan()
