from langchain.agents import Tool

from llm_osint import cache_utils


from typing import Any, Dict, List, Optional
from urllib.request import build_opener, ProxyHandler, Request
from langchain.pydantic_v1 import BaseModel, root_validator

from typing_extensions import Literal
import aiohttp
import json
import os


class BrightDataSerperAPIWrapper(BaseModel):
    k: int = 10
    gl: str = "us"
    hl: str = "en"
    type: Literal["news", "search", "places", "images"] = "search"
    brightdata_api_key: Optional[str] = None
    host: str
    username: str
    password: str
    aiosession: Optional[aiohttp.ClientSession] = None
    result_key_for_type = {
        "news": "news",
        "places": "places",
        "images": "images",
        "search": "organic",
    }

    class Config:
        arbitrary_types_allowed = True

    def results(self, query: str, **kwargs: Any) -> Dict:
        return self._brightdata_serper_api_results(query, **kwargs)

    async def aresults(self, query: str, **kwargs: Any) -> Dict:
        return await self._async_brightdata_serper_api_results(query, **kwargs)

    def run(self, query: str, **kwargs: Any) -> str:
        results = self._brightdata_serper_api_results(query, **kwargs)
        return self._parse_results(results)

    async def arun(self, query: str, **kwargs: Any) -> str:
        results = await self._async_brightdata_serper_api_results(query, **kwargs)
        return self._parse_results(results)

    def _brightdata_serper_api_results(self, search_term: str, **kwargs: Any) -> dict:
        proxy_url = f"http://{self.username}:{self.password}@{self.host}"
        opener = build_opener(ProxyHandler({'http': proxy_url, 'https': proxy_url}))
        
        headers = {
            "X-API-KEY": self.brightdata_api_key or "",
            "Content-Type": "application/json",
        }
        
        params = {
            "q": search_term,
            **{key: value for key, value in kwargs.items() if value is not None},
        }
        
        full_url = f"{self.host}/search_type?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
        
        request = Request(full_url, headers=headers)
        response = opener.open(request)
        search_results = json.loads(response.read().decode('utf-8'))
        
        return search_results

    async def _async_brightdata_serper_api_results(self, search_term: str, **kwargs: Any) -> dict:
        proxy_url = f"http://{self.username}:{self.password}@{self.host}"
        headers = {
            "X-API-KEY": self.brightdata_api_key or "",
            "Content-Type": "application/json",
        }
        
        params = {
            "q": search_term,
            **{key: value for key, value in kwargs.items() if value is not None},
        }
        
        full_url = f"{self.host}/search_type?{'&'.join([f'{k}={v}' for k, v in params.items()])}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url, proxy=proxy_url, headers=headers) as response:
                search_results = await response.json()
        
        return search_results


    def _parse_snippets(self, results: dict) -> List[str]:
        snippets = []

        # Parsing Knowledge Graph, if available
        kg = results.get("knowledgeGraph", {})
        if kg:
            title = kg.get("title")
            type_ = kg.get("type")
            description = kg.get("description")
            snippets.append(f"Knowledge Graph: {title} ({type_}) - {description}")

            # Adding attributes from Knowledge Graph
            for attr, value in kg.get("attributes", {}).items():
                snippets.append(f"{attr}: {value}")

        # Parsing organic search results
        for result in results.get("organic", [])[:self.k]:
            snippet = result.get("snippet")
            if snippet:
                snippets.append(snippet)
            
            title = result.get("title")
            if title:
                snippets.append(f"Title: {title}")

            link = result.get("link")
            if link:
                snippets.append(f"URL: {link}")

        # Parsing 'People Also Ask'
        paa = results.get("peopleAlsoAsk", [])
        for item in paa:
            question = item.get("question")
            answer = item.get("snippet")
            snippets.append(f"People Also Ask: {question} - {answer}")

        # Parsing 'Related Searches'
        rs = results.get("relatedSearches", [])
        for item in rs:
            query = item.get("query")
            snippets.append(f"Related Search: {query}")

        if len(snippets) == 0:
            return ["No good search result was found"]
            
        return snippets

    def _parse_results(self, results: dict) -> str:
        return " ".join(self._parse_snippets(results))


class GoogleSerperSearchWrapper(BrightDataSerperAPIWrapper):
    @cache_utils.cache_func
    def run(self, query: str) -> str:
        return super().run(query)

    def _parse_results(self, results: dict) -> str:
        snippets = []

        if results.get("answerBox"):
            answer_box = results.get("answerBox", {})
            if answer_box.get("answer"):
                return answer_box.get("answer")
            elif answer_box.get("snippet"):
                return answer_box.get("snippet").replace("\n", " ")
            elif answer_box.get("snippetHighlighted"):
                return ", ".join(answer_box.get("snippetHighlighted"))

        if results.get("knowledgeGraph"):
            kg = results.get("knowledgeGraph", {})
            title = kg.get("title")
            entity_type = kg.get("type")
            if entity_type:
                snippets.append(f"{title}: {entity_type}.")
            description = kg.get("description")
            if description:
                snippets.append(description)
            for attribute, value in kg.get("attributes", {}).items():
                snippets.append(f"{title} {attribute}: {value}.")

        for result in results["organic"][: self.k]:
            if "snippet" in result:
                snippets.append(f'{result["title"]}: {result["snippet"]} (link {result["link"]})')
            for attribute, value in result.get("attributes", {}).items():
                snippets.append(f'{result["title"]}: {attribute} = {value}.')

        if len(snippets) == 0:
            return "No good results found"

        return "\n\n".join(snippets)


def get_search_tool(**kwargs) -> Tool:
    search = GoogleSerperSearchWrapper(**kwargs)
    return Tool(
        name="Search Term",
        func=search.run,
        description="useful for when you need to find information about general things, names, usernames, places, etc. the input should be a search term",
    )

from llm_osint.tools.search import GoogleSerperSearchWrapper


# Ensure required environment variables are set
required_env_vars = ["YOUR_HOST", "YOUR_USERNAME", "YOUR_PASSWORD"]
for env_var in required_env_vars:
    if env_var not in os.environ:
        raise ValueError(f"Environment variable {env_var} is not set.")


# Provide the required information
search_tool = GoogleSerperSearchWrapper(
    host=os.environ["YOUR_HOST"],
    username=os.environ["YOUR_USERNAME"],
    password=os.environ["YOUR_PASSWORD"]
)
