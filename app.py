import time
import json
import requests
import urllib.parse
import re
import urllib.request
import streamlit as st

from pydantic import BaseModel
from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError
from PIL import Image

# ==========================================
# PAGE CONFIG & SETUP
# ==========================================
st.set_page_config(page_title="Vibify | AI Synesthesia", page_icon="🎧", layout="centered")
API_KEY = st.secrets["GEMINI_API_KEY"]
client = genai.Client(api_key=API_KEY)


# ==========================================
# DATA SCHEMAS
# ==========================================
class Track(BaseModel):
    title: str
    artist: str


class Playlist(BaseModel):
    vibe_summary: str
    artist_note: str
    color_palette: list[str]
    tracks: list[Track]


# ==========================================
# HELPER FUNCTIONS
# ==========================================
def get_youtube_video(query: str) -> str | None:
    """Scrapes YouTube for the first matching video ID."""
    try:
        query_string = urllib.parse.urlencode({"search_query": query + " official audio"})
        html_content = urllib.request.urlopen("https://www.youtube.com/results?" + query_string, timeout=3)
        search_results = re.findall(r'/watch\?v=(.{11})', html_content.read().decode())
        if search_results:
            return f"https://www.youtube.com/watch?v={search_results[0]}"
    except Exception as e:
        print(f"YouTube search error: {e}")
    return None


def get_itunes_artwork(title: str, artist: str) -> str:
    """Fetches high-res album covers from iTunes."""
    itunes_url = "https://itunes.apple.com/search"
    params = {"term": f"{title} {artist}", "entity": "song", "limit": 1}
    try:
        resp = requests.get(itunes_url, params=params, timeout=3).json()
        results = resp.get("results", [])
        if results:
            return results[0].get("artworkUrl100", "").replace("100x100bb", "400x400bb")
    except Exception:
        pass
    return ""


def fetch_weather(location: str):
    """Handles geocoding and weather fetching."""
    geocode_url = f"https://geocoding-api.open-meteo.com/v1/search?name={location.strip()}&count=1"
    geo_resp = requests.get(geocode_url, timeout=3).json()

    if not geo_resp.get("results"):
        return None, f"❌ '{location}' is not a recognized city."

    official_city = geo_resp["results"][0]["name"]
    user_input, api_city = location.strip().lower(), official_city.lower()

    if user_input not in api_city and api_city not in user_input:
        return None, f"❌ '{location}' is not a recognized city name."

    admin = geo_resp["results"][0].get("admin1", "")
    country = geo_resp["results"][0].get("country", "")
    full_location = f"{official_city}, {admin}, {country}" if admin else f"{official_city}, {country}"

    wttr_url = f"https://wttr.in/{official_city}?format=%C+%t"
    weather_resp = requests.get(wttr_url, timeout=3)
    weather_data = weather_resp.text

    if weather_resp.status_code == 404 or "Unknown location" in weather_data or "<html" in weather_data:
        return None, "❌ Weather data currently unavailable for this location."

    return (full_location, weather_data), None


def inject_custom_css(colors: list[str]):
    css = f"""
    <style>
    .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{
        background: linear-gradient(135deg, {colors[0]} 0%, {colors[1]} 100%) !important;
        background-attachment: fixed !important;
        transition: background 1.5s ease-in-out;
    }}
    [data-testid="stHeader"] {{ background: transparent !important; }}

    /* Global Text Outline */
    p, h1, h2, h3, span, a, label, div, [data-testid="stMarkdownContainer"] * {{
        text-shadow: -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000, 1px 1px 0 #000, 0px 2px 4px #000 !important;
        color: white !important;
    }}

    /* Container Styling: Spacing and Outlines */
    div[data-testid="stForm"], 
    div[data-testid="stVerticalBlockBorderWrapper"],
    div[data-testid="stVerticalBlockBorderWrapper"] > div,
    fieldset, 
    div[data-testid="stAlert"] {{
        background-color: rgba(14, 17, 23, 0.65) !important;
        border: 2px solid #000000 !important;
        border-color: #000000 !important;
        box-shadow: 4px 4px 14px rgba(0, 0, 0, 0.6) !important;
        border-radius: 12px !important;
        backdrop-filter: blur(8px);
        padding: 0.5rem;
    }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


# ==========================================
# MAIN APP UI
# ==========================================
def main():
    # Header
    st.markdown("""
        <div style="display: flex; justify-content: center; align-items: center; gap: 20px; padding-bottom: 10px; margin-top: -40px;">
            <div style="font-size: 65px; filter: drop-shadow(0px 4px 5px rgba(0,0,0,0.8));">🎧</div>
            <h1 style="font-size: 80px; font-weight: 900; margin: 0; letter-spacing: -2px;">Vibify</h1>
        </div>
        """, unsafe_allow_html=True)
    st.markdown("<p style='text-align: center;'>Use Gemini to generate a playlist based on your current vibe!</p>", unsafe_allow_html=True)

    # Input Form
    input_type = st.radio("Choose your vibe source:", ["Image Upload", "Local Weather", "Color Picker"],
                          horizontal=True)

    with st.form("playlist_form", border=True):
        col1, col2 = st.columns([1, 1])  # Splits the form for a cleaner look

        with col1:
            if input_type == "Image Upload":
                uploaded_file = st.file_uploader("Upload an image", type=["jpg", "jpeg", "png"])
                location, selected_color = None, None
            elif input_type == "Local Weather":
                location = st.text_input("Enter your city", placeholder="e.g., Seattle, Tokyo...")
                uploaded_file, selected_color = None, None
            else:
                selected_color = st.color_picker("Pick a color vibe:", "#1DB954")
                uploaded_file, location = None, None

        with col2:
            artist_preference = st.text_input("Preferred Artist (Optional)", placeholder="e.g., Frank Ocean...")
            st.markdown("<br>", unsafe_allow_html=True)  # Spacer
            submitted = st.form_submit_button("✨ Generate Playlist", use_container_width=True)

    # Processing Logic
    if submitted:
        if input_type == "Image Upload" and not uploaded_file:
            st.error("Please upload an image first!")
            return
        if input_type == "Local Weather" and not location.strip():
            st.error("Please enter a city!")
            return

        with st.spinner("Analyzing the vibe and compiling tracks..."):
            prompt_contents = []

            if input_type == "Image Upload":
                image = Image.open(uploaded_file)
                st.image(image, caption="Uploaded Image", use_container_width=True)
                base_prompt = "Analyze the visual mood and lighting of this image. Write a 1-sentence poetic summary of the vibe. Select exactly two hex color codes that represent this image to be used as a background gradient."
                prompt_contents = [base_prompt, image]

            elif input_type == "Local Weather":
                weather_info, error = fetch_weather(location)
                if error:
                    st.error(error)
                    return
                full_loc, weather_str = weather_info
                st.info(f"⛅ Current weather in {full_loc}: {weather_str}")
                base_prompt = f"The user is in {full_loc} and the current weather is {weather_str}. Analyze the mood of this weather. Write a 1-sentence poetic summary of the vibe. Select exactly two hex color codes that represent this weather to be used as a background gradient."
                prompt_contents = [base_prompt]

            elif input_type == "Color Picker":
                st.markdown(f"🎨 **Selected Color:** `{selected_color}`")
                base_prompt = f"The user selected the hex color {selected_color}. Analyze the psychological mood of this color. Write a 1-sentence poetic summary of the vibe. Select exactly two hex color codes for a background gradient, ensuring {selected_color} is one of them."
                prompt_contents = [base_prompt]

            if artist_preference.strip():
                prompt_contents[
                    0] += f" The user requested the artist '{artist_preference.strip()}'. If real, select 5 songs strictly by them matching the vibe. If unrecognized, ignore it and pick 5 songs from ANY artist. Note your actions in 'artist_note'."
            else:
                prompt_contents[
                    0] += " Generate a playlist of exactly 5 real songs from any artist that perfectly match this vibe. Set 'artist_note' to 'No artist specified.'"


            max_retries = 10
            playlist_data = None

            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model='gemini-3.5-flash',
                        contents=prompt_contents,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=Playlist,
                            temperature=0.7,
                        )
                    )
                    playlist_data = json.loads(response.text)
                    break

                except (ServerError, ClientError) as e:
                    if attempt < max_retries - 1:
                        st.toast(f"Network or Quota busy. Retrying... (Attempt {attempt + 1}/{max_retries})")
                        time.sleep(5)
                    else:
                        st.error("The AI is currently overloaded. Please try again later.")
                        return


            if playlist_data:
                colors = playlist_data.get("color_palette", ["#090A0F", "#1A1C23"])
                if len(colors) < 2: colors.append("#090A0F")

                inject_custom_css(colors)
                st.success("Playlist Generated Successfully!")

                with st.container(border=True):
                    st.markdown(f"**The Vibe:** {playlist_data['vibe_summary']}")
                    if artist_preference.strip():
                        st.info(f"**Note:** {playlist_data['artist_note']}")

                for track in playlist_data["tracks"]:
                    title, artist = track["title"], track["artist"]
                    artwork = get_itunes_artwork(title, artist)
                    youtube_url = get_youtube_video(f"{title} {artist}")
                    spotify_link = f"https://open.spotify.com/search/{urllib.parse.quote(f'{title} {artist}')}"

                    with st.container(border=True):
                        col1, col2 = st.columns([1, 4])
                        with col1:
                            if artwork: st.image(artwork, use_container_width=True)
                        with col2:
                            st.markdown(f"### {title}")
                            st.markdown(f"**{artist}**")
                            if youtube_url:
                                st.video(youtube_url)
                            else:
                                st.caption("Full audio not available on YouTube.")
                            st.markdown(f"[▶️ Open in Spotify]({spotify_link})")


if __name__ == "__main__":
    main()