from loguru import logger
from typing import Optional
from agentchat.services.rag.retrieval import MixRetrival
from agentchat.services.rag.result_merger import merge_documents_by_score
from agentchat.services.rewrite.query_write import query_rewriter
from agentchat.services.rag.es_client import client as es_client
from agentchat.services.rag.vector_stores import milvus_client
from agentchat.services.rag.rerank import Reranker
from agentchat.settings import app_settings

class RagHandler:

    @classmethod
    async def query_rewrite(cls, query):
        query_list = await query_rewriter.rewrite(query)
        return query_list

    @classmethod
    async def _retrieve_field_results(cls, query_list, knowledges_id, search_field, index_names=None):
        if app_settings.rag.enable_elasticsearch:
            es_documents, milvus_documents = await MixRetrival.mix_retrival_documents(
                query_list, knowledges_id, search_field, index_names
            )
            es_documents.sort(key=lambda doc: doc.score, reverse=True)
            milvus_documents.sort(key=lambda doc: doc.score, reverse=True)
            return es_documents + milvus_documents
        return await MixRetrival.retrival_milvus_documents(
            query_list, knowledges_id, search_field
        )

    @classmethod
    async def index_milvus_documents(cls, collection_name, chunks):
        await milvus_client.insert(collection_name, chunks)

    @classmethod
    async def index_es_documents(cls, index_name, chunks):
        await es_client.index_documents(index_name, chunks)

    @classmethod
    async def mix_retrival_documents(cls, query_list, knowledges_id, search_field="summary", index_names=None):

        if search_field == "content+summary":
            content_documents = await cls._retrieve_field_results(
                query_list, knowledges_id, "content", index_names
            )
            summary_documents = await cls._retrieve_field_results(
                query_list, knowledges_id, "summary", index_names
            )
            all_documents = content_documents + summary_documents
        else:
            all_documents = await cls._retrieve_field_results(
                query_list, knowledges_id, search_field, index_names
            )

        documents = merge_documents_by_score(all_documents, top_k=10)
        return documents

    @classmethod
    def _filter_reranked_documents(cls, reranked_docs, min_score, rerank_threshold, top_k):
        filtered_results = []
        for doc in reranked_docs:
            if min_score is not None and doc.score < min_score:
                continue
            if rerank_threshold is not None and doc.score < rerank_threshold:
                continue
            filtered_results.append(doc)
        actual_top_k = top_k if top_k is not None else len(filtered_results)
        return filtered_results[:actual_top_k]

    @classmethod
    async def rag_query_summary(cls, query, knowledges_id, min_score: Optional[float]=None,
                                top_k: Optional[int]=None, needs_query_rewrite: bool=True,
                                rerank_threshold: Optional[float]=None):
        if min_score is None:
            min_score = app_settings.rag.retrival.get('min_score')
        if top_k is None:
            top_k = app_settings.rag.retrival.get('top_k')
        if rerank_threshold is None:
            rerank_threshold = app_settings.rag.retrival.get('rerank_threshold')

        # 查询重写
        if needs_query_rewrite:
            rewritten_queries = await cls.query_rewrite(query)
        else:
            rewritten_queries = [query]

        # 文档检索
        retrieved_documents = await cls.mix_retrival_documents(rewritten_queries, knowledges_id, "summary")

        # 准备重排序的文档内容
        documents_to_rerank = [doc.content for doc in retrieved_documents]

        # 文档重排序
        reranked_docs = await Reranker.rerank_documents(query, documents_to_rerank)

        # 确保top_k不为None
        actual_top_k = top_k if top_k is not None else len(reranked_docs)
        if actual_top_k and len(reranked_docs) < actual_top_k:
            logger.info("Recall for summary field numbers < top k, start recall using content field")
            return await cls.retrieve_ranked_documents(
                query,
                knowledges_id,
                min_score=min_score,
                top_k=top_k,
                needs_query_rewrite=needs_query_rewrite,
                rerank_threshold=rerank_threshold,
            )

        filtered_results = cls._filter_reranked_documents(
            reranked_docs, min_score, rerank_threshold, top_k
        )
        final_result = "\n".join(result.content for result in filtered_results)
        return final_result


    @classmethod
    async def retrieve_ranked_documents(cls, query, collection_names, index_names=None, min_score: Optional[float]=None,
                        top_k: Optional[int]=None, needs_query_rewrite: bool=True,
                        rerank_threshold: Optional[float]=None):
        """
        处理 RAG 流程：查询重写、文档检索、重排序、结果过滤和拼接。

        Args:
            query (str): 用户查询。
            collection_names (list[str]): 向量知识库 集合ID。
            index_names (list[str]): ES关键词库 集合ID。
            min_score (float): 文档最低分数阈值，默认为配置中的值。
            top_k (int): 召回文档的个数。
            needs_query_rewrite (bool): 是否需要开启Query重写，默认开启
            rerank_threshold (float): 重排序后最低分数阈值，默认为配置中的值。

        Returns:
            str: 拼接后的最终结果。
            """
        if min_score is None:
            min_score = app_settings.rag.retrival.get('min_score')
        if top_k is None:
            top_k = app_settings.rag.retrival.get('top_k')
        if rerank_threshold is None:
            rerank_threshold = app_settings.rag.retrival.get('rerank_threshold')

        # 查询重写
        if needs_query_rewrite:
            rewritten_queries = await cls.query_rewrite(query)
        else:
            rewritten_queries = [query]

        # 文档检索
        retrieved_documents = await cls.mix_retrival_documents(
            rewritten_queries, collection_names, "content", index_names
        )

        # 准备重排序的文档内容
        documents_to_rerank = [doc.content for doc in retrieved_documents]

        # 文档重排序
        reranked_docs = await Reranker.rerank_documents(query, documents_to_rerank)

        filtered_results = cls._filter_reranked_documents(
            reranked_docs, min_score, rerank_threshold, top_k
        )

        # 处理空结果
        if not filtered_results:
            return "No relevant documents found."

        # 拼接最终结果
        final_result = "\n".join(result.content for result in filtered_results)
        return final_result

    @classmethod
    async def delete_documents_es_milvus(cls, file_id, knowledge_id):
        if app_settings.rag.enable_elasticsearch:
            await es_client.delete_documents(file_id, knowledge_id)
        await milvus_client.delete_by_file_id(file_id, knowledge_id)
