# localai | This file is a recreation of the `openai` library, with the backend calling ollama models running locally
# for now, it calls models that work on the local machine,
# and soon we will implement a system of workers onto whom requests will be offloaded.
# the reason this library is so convoluted, replicating every unnecessary aspect of OpenAI's library
# is because i want to eventually get this file working as a standalone library, as well as keeping my development debt
# as low as possible, and avoiding quick hacks has been reliably the best method for me for avoiding unnecessary bugs.

from typing import List, Optional
from typing_extensions import Literal

import json
import math
import time

# Update: new openai api natively supports choosing ai-server, thus i will be switching to recreating that
# Additionally, after some planning it seems like both approaches may be equally difficult to implement,
# and so I will try writing the approach #2 straight away.

import requests
from types import SimpleNamespace
from dataclasses import dataclass


# todo: write a worker-app
# todo: add a system automatically choosing the most appropriate model, handle this in the worker-app
# due to how 'client' is initialized in other files, distribution will be handled here via a separate singleton class
# todo: append RUN_LOCALLY logic to every instance of openai and OPENAI_API_KEY inside the CAMEL folder (recursive)

# all of the following classes: LocalChatCompletionMessage, LocalCompletionUsage, LocalChoice, LocalChatCompletion
# are used solely for the purpose of type recognition in chat_agent.py
@dataclass
class LocalChatCompletionMessage:
    content: Optional[str]  # The contents of the message.
    role: Literal["assistant"]  # The role of the author of this message.

    # function_call: Optional[FunctionCall] = None
    """Deprecated and replaced by `tool_calls`.

    The name and arguments of a function that should be called, as generated by the
    model.
    """

    # fixme: this is a very important feature
    # tool_calls: Optional[List[ChatCompletionMessageToolCall]] = None


@dataclass
class LocalCompletionUsage:
    prompt_tokens: str
    completion_tokens: str
    total_tokens: str


@dataclass
class LocalChoice:
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "function_call"]
    """The reason the model stopped generating tokens.

    This will be `stop` if the model hit a natural stop point or a provided stop
    sequence, `length` if the maximum number of tokens specified in the request was
    reached, `content_filter` if content was omitted due to a flag from our content
    filters, `tool_calls` if the model called a tool, or `function_call`
    (deprecated) if the model called a function.
    """

    index: int
    """The index of the choice in the list of choices."""

    message: LocalChatCompletionMessage
    """A chat completion message generated by the model."""


@dataclass
class LocalChatCompletion:
    id: str  # A unique identifier for the chat completion.

    choices: List[LocalChoice]  # A list of chat completion choices.

    created: int  # The Unix timestamp (in seconds) of when the chat completion was created.

    model: str  # The model used for the chat completion.

    object: Literal["chat.completion"]  # The object type, which is always `chat.completion`.

    system_fingerprint: Optional[str] = None
    """This fingerprint represents the backend configuration that the model runs with.

    Can be used in conjunction with the `seed` request parameter to understand when
    backend changes have been made that might impact determinism.
    """

    usage: Optional[LocalCompletionUsage] = None  # Usage statistics for the completion request.


# a basic singleton implementation, communicates with all the worker agents
class WorkerManagerMetaclass:
    _instances = {}

    def __call__(self, *args, **kwargs):
        if self not in self._instances:
            self._instances[self] = super(WorkerManagerMetaclass, self).__call__(*args, **kwargs)
        return self._instances[self]


# won't be used for now, first we have to get rest of the functionality working.
class WorkerManager(WorkerManagerMetaclass):
    def __init__(self, data):
        self.data = data


class LocalAI:
    class Chat:
        class Completions:
            # todo: replace with all parameters that are mentioned in either web_spider.py or model_backend.py
            def create(self, user, messages, max_tokens, *args, **kwargs):
                # all supplied kwargs: ['messages', 'model', 'temperature', 'top_p', 'n', 'stream', 'stop',
                # 'max_tokens', 'presence_penalty', 'frequency_penalty', 'logit_bias', 'user']

                request_url = self.parent.parent.base_url + 'api/chat'  # 'generate' can be used for one-turn chat only
                # this broken formatting wraps the json inside the key of our html form,
                # this is the only accepted formatting by ollama
                # request_headers = {"Content-Type": "application/x-www-form-urlencoded"}
                request_data = {
                    'model': self.parent.parent.model,
                    'messages': messages,
                }

                response = requests.post(url=request_url, json=request_data, stream=True)
                response.raise_for_status()

                response_stream = response.iter_lines()
                response_list = []

                # a frequent bug with llama-uncensored2 is to have a soft-locked loop of the '\n' token being returned
                # in order to avoid this we have to switch to a stream mode, parsing every incoming token individually
                repeat_counter = 0
                repeat_token = ''

                prompt_token_count = 0
                response_token_count = 0

                # convert response to json:
                for chunk in response_stream:
                    chunk_json = json.loads(chunk)
                    chunk_text = chunk_json['message']['content']
                    chunk_done = chunk_json['done']

                    if chunk_text == repeat_token:
                        repeat_counter += 1
                    else:
                        repeat_counter = 0
                        repeat_token = chunk_text

                    if chunk_done:
                        # final chunk contains special information
                        prompt_token_count = chunk_json['prompt_eval_count']
                        prompt_token_count = chunk_json['eval_count']
                        response.close()
                        break
                    elif repeat_counter > 5:
                        response.close()
                        break
                    else:
                        response_list.append(chunk_text)

                response_text = ''.join(response_list)

                print('agent response submitted')
                print('text:', response_text)

                # token estimations, todo: make use of the llama tokenizer
                input_cost = prompt_token_count
                output_cost = response_token_count
                total_cost = input_cost + output_cost

                # replicate the entire returned object: https://platform.openai.com/docs/api-reference/chat/object
                # todo: i may need to convert this simple namespace into a ChatCompletion class
                # SimpleNamespace just creates an object in place, it's like having a no-name class
                return_object = LocalChatCompletion(
                    id=str(round(time.time() * 1000)),  # fixme: timestamp is not really a proper id, works for now
                    object='chat.completion',
                    created=round(time.time() * 1000),
                    model=self.parent.parent.model,
                    system_fingerprint='system_fingerprint-stud',
                    choices=[
                        LocalChoice(
                            # this section requires some testing to be completed
                            # so far it seems to be sufficient to only have one response here,
                            # but having more alternative choices should work as well
                            index=0,
                            message=LocalChatCompletionMessage(
                                role='assistant',
                                content=response_text
                            ),
                            finish_reason='stop'
                        )
                    ],
                    usage=LocalCompletionUsage(
                        # this section requires some testing to be completed
                        prompt_tokens=str(input_cost),
                        completion_tokens=str(output_cost),  # a very rough, pessimistic estimate
                        total_tokens=str(total_cost)
                    )
                )

                print('returning')
                print('^ object:', return_object)

                return return_object

            def __init__(self, parent):
                self.parent = parent

        def __init__(self, parent):
            self.parent = parent

            # Create instances of all nested classes
            self.completions = self.Completions(self)

    def __init__(self, base_url=None, decentralize=False):
        # base_url will only ever be used when DECENTRALIZE is set to 0 or not set at all
        if base_url:
            self.base_url = base_url
        else:
            self.base_url = 'http://192.168.1.49:8088/'

        # tested 3 models in total:
        """
            openhermes: the best quality of responses so far, flawless python code, great implementation and tolerable speed.
            dolphin-phi: tiny model which maintains great quality of responses, while being the fastest of all usable models i tried. It's responses are errorless but have trouble with indentation.
            llama2-undensored:7b: a bit faster than hermes, comparable in quality to dolphin-phi, definietely the worst of the 3 mentioned, but also manages simpler tasks well
            additional note for openhermes:
                the quality of responses is really phenomenal, far surpassing ChatGPT 3.5, as well as the models mentioned above
                it's speed is comparable to the llama2
                it's amazing at commenting and documenting as well, besides buglessly performing complicated tasks on the first try,
                even going as far as mixing mutliple languages and linking them together, it places comments in a tasteful and balaced way.
        """
        self.model = 'phi4:14b-q8_0'  # todo: move model selection to the worker-app

        # Create instances of all nested classes
        self.chat = self.Chat(self)