import requests
from flask import Flask, render_template, request, jsonify
from youtube_transcript_api import YouTubeTranscriptApi
from openai import OpenAI
# from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Pool, Manager


app = Flask(__name__)

API_KEY = ""
CHATGPT_API_KEY = ""
client = OpenAI(api_key=CHATGPT_API_KEY)

def fetch_video_data(args):
    video_id, title, orig_query = args
    comments = None # get_comments(video_id)
    transcript = get_transcript(video_id)
    transcript = title + "\n\n" + transcript if transcript else title

    relevance_score = assess_relevance(transcript, orig_query) if transcript else 0

    return (video_id, comments, transcript, relevance_score)

def search_youtube(query, max_results=5, orig_query=None):
    if not orig_query:
        orig_query = query
    print("Original query:", orig_query)
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        'part': 'snippet',
        'q': query,
        'type': 'video',
        'maxResults': max_results,
        'key': API_KEY
    }
    print("Query:", query)
    response = requests.get(url, params=params)
    
    if response.status_code == 200:
        items = response.json().get('items', [])
        videos = []
        
        # Create a multiprocessing pool
        with Pool(processes=8) as pool:
            # Prepare video IDs for processing
            video_ids = [item['id']['videoId'] for item in items]
            titles = [item['snippet']['title'] for item in items]
            thumbnails = [item['snippet']['thumbnails']['high']['url'] for item in items]

            # Map video IDs to fetching function
            results = pool.map(fetch_video_data, zip(video_ids, titles, [orig_query]*len(video_ids)))
            # print(results) 
            # Construct the videos list with titles and thumbnails
            for i, (video_id, comments, transcript, relevance_score) in enumerate(results):
                videos.append({
                    'title': titles[i],
                    'videoId': video_id,
                    'thumbnail': thumbnails[i],
                    'comments': comments,
                    'transcript': transcript[:2000] if transcript else "Transcript not available",
                    'relevance_score': relevance_score
                })
        # if transcript:  
        #     print(transcript[:2000])
        # for video in videos:
        #     print(video['title'])
        return videos
    else:
        print("Failed to fetch data:", response.status_code)
        return None

def assess_relevance(transcript, query):
    # Call OpenAI API to assess the relevance of the transcript to the query
    prompt = f"Rate the relevance of the following transcript to the query '{query}':\n\nTranscript: {transcript}. Output just a number from 1 to 10.\n\nRelevance Score (1-10):"
    # load prompt.txt file
    with open("relevance_prompt.txt", "r") as file:
        prompt = file.read()
    prompt = prompt.format(transcript=transcript, query=query)

    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    # Assuming the completion response gives a score as plain text
    score = completion.choices[0].message.content.strip().replace("Rating:", "").strip()
    try: 
        score = float(score)
    except:
        print('Help.... ', score)
        score = 0
    print("Transcript:", transcript[:100])
    print("Relevance Score:", score)
    return score


def refine_query_with_chatgpt(query):
    # load prompt.txt file
    with open("prompt.txt", "r") as file:
        prompt = file.read()
    # append user query to prompt
    prompt += f"{query}\nEnriched Keywords: "

    completion = client.chat.completions.create(
        model="o1-mini",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    )
    return completion.choices[0].message.content


def get_comments(video_id, max_comments=5):
    url = "https://www.googleapis.com/youtube/v3/commentThreads"
    params = {
        'part': 'snippet',
        'videoId': video_id,
        'maxResults': max_comments,
        'key': API_KEY
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return [
            item['snippet']['topLevelComment']['snippet']['textDisplay']
            for item in response.json().get('items', [])
        ]
    else:
        print("Failed to fetch comments:", response.status_code)
        return None

def get_view_count(video_id):
    url = "https://www.googleapis.com/youtube/v3/videos"
    params = {
        'part': 'statistics',
        'id': video_id,
        'key': API_KEY
    }
    
    response = requests.get(url, params=params)
    if response.status_code == 200:
        items = response.json().get('items', [])
        if items:
            return items[0]['statistics'].get('viewCount', 'View count not available')
        else:
            print("No video found with the provided ID.")
            return None
    else:
        print("Failed to fetch view count:", response.status_code)
        return None

def get_transcript(video_id):
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        return " ".join([text['text'] for text in transcript])
    except Exception as e:
        print(f"Failed to fetch transcript: {str(e)[:100]}")
        return None


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/search', methods=['POST'])
def search():
    orig_query = request.form['query']
    print("Original query:", orig_query)
    query = refine_query_with_chatgpt(orig_query)
    query = query.replace("Enriched Keywords:", "")
    print("Refined query:", query)
    results = []
    new_q_list = list(set([orig_query] + query.split(";")))
    if len(new_q_list) > 1:
        for q in new_q_list:
            results.extend(search_youtube(q, orig_query=orig_query))
    else:
        results = search_youtube(query, orig_query=orig_query, max_results=10)
    # deduplicate results by videoId
    results = list({video['videoId']: video for video in results}.values())
    # results = search_youtube(query)
    # results = sorted(results, key=lambda x: int(x['views']), reverse=True)
    # Rank videos by transcript relevance
    ranked_results = sorted(results, key=lambda x: x['relevance_score'], reverse=True)
    if ranked_results:
        return jsonify(ranked_results)
    else:
        return jsonify([])

if __name__ == '__main__':
    app.run(debug=True)
