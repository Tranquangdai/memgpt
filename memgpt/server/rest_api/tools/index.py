import uuid
from functools import partial
from typing import List, Literal, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from memgpt.models.pydantic_models import ToolModel
from memgpt.server.rest_api.auth_token import get_current_user
from memgpt.server.rest_api.interface import QueuingInterface
from memgpt.server.server import SyncServer

router = APIRouter()


class ListToolsResponse(BaseModel):
    tools: List[ToolModel] = Field(..., description="List of tools (functions).")


class CreateToolRequest(BaseModel):
    json_schema: dict = Field(..., description="JSON schema of the tool.")  # NOT OpenAI - just has `name`
    source_code: str = Field(..., description="The source code of the function.")
    source_type: Optional[Literal["python"]] = Field(None, description="The type of the source code.")
    tags: Optional[List[str]] = Field(None, description="Metadata tags.")
    update: Optional[bool] = Field(False, description="Update the tool if it already exists.")


class CreateToolResponse(BaseModel):
    tool: ToolModel = Field(..., description="Information about the newly created tool.")


def setup_user_tools_index_router(server: SyncServer, interface: QueuingInterface, password: str):
    get_current_user_with_server = partial(partial(get_current_user, server), password)

    @router.delete("/tools/{tool_name}", tags=["tools"])
    async def delete_tool(
        tool_name: str,
        user_id: uuid.UUID = Depends(get_current_user_with_server),
    ):
        """
        Delete a tool by name
        """
        # Clear the interface
        interface.clear()
        # tool = server.ms.delete_tool(user_id=user_id, tool_name=tool_name) TODO: add back when user-specific
        server.ms.delete_tool(name=tool_name, user_id=user_id)

    @router.get("/tools/{tool_name}", tags=["tools"], response_model=ToolModel)
    async def get_tool(
        tool_name: str,
        user_id: uuid.UUID = Depends(get_current_user_with_server),
    ):
        """
        Get a tool by name
        """
        # Clear the interface
        interface.clear()
        tool = server.ms.get_tool(tool_name=tool_name, user_id=user_id)
        if tool is None:
            # return 404 error
            raise HTTPException(status_code=404, detail=f"Tool with name {tool_name} not found.")
        return tool

    @router.get("/tools", tags=["tools"], response_model=ListToolsResponse)
    async def list_all_tools(
        user_id: uuid.UUID = Depends(get_current_user_with_server),
    ):
        """
        Get a list of all tools available to agents created by a user
        """
        # Clear the interface
        interface.clear()
        tools = server.ms.list_tools(user_id=user_id)
        return ListToolsResponse(tools=tools)

    @router.post("/tools", tags=["tools"], response_model=ToolModel)
    async def create_tool(
        request: CreateToolRequest = Body(...),
        user_id: uuid.UUID = Depends(get_current_user_with_server),
    ):
        """
        Create a new tool
        """
        # NOTE: horrifying code, should be replaced when we migrate dev portal
        from memgpt.agent import Agent  # nasty: need agent to be defined
        from memgpt.functions.schema_generator import generate_schema

        name = request.json_schema["name"]

        import ast

        parsed_code = ast.parse(request.source_code)
        function_names = []

        # Function to find and print function names
        def find_function_names(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef):
                    # Print the name of the function
                    function_names.append(child.name)
                # Recurse into child nodes
                find_function_names(child)

        # Find and print function names
        find_function_names(parsed_code)
        assert len(function_names) == 1, f"Expected 1 function, found {len(function_names)}: {function_names}"

        # generate JSON schema
        env = {}
        env.update(globals())
        exec(request.source_code, env)
        func = env.get(function_names[0])
        json_schema = generate_schema(func, name=name)
        from pprint import pprint

        pprint(json_schema)

        try:

            return server.create_tool(
                # json_schema=request.json_schema, # TODO: add back
                json_schema=json_schema,
                source_code=request.source_code,
                source_type=request.source_type,
                tags=request.tags,
                user_id=user_id,
                exists_ok=request.update,
            )
        except Exception as e:
            print(e)
            raise HTTPException(status_code=500, detail=f"Failed to create tool: {e}, exists_ok={request.update}")

    return router
