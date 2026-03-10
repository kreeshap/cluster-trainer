import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Load locally for testing, Render will provide these automatically in production
load_dotenv()

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_ANON_KEY")

supabase: Client = create_client(url, key)

# Example: Fetching your Clusters
def get_clusters():
    response = supabase.table("clusters").select("*").execute()
    return response.data