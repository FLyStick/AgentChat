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
    """RAG 检索处理核心：负责查询改写、多路召回、重排序与结果过滤拼接。"""

    @staticmethod
    def _normalize_rewritten_queries(query, rewritten_queries):
        """规范化改写后的查询列表：去重并保证原始 query 在最前。"""
        if isinstance(rewritten_queries, str):
            rewritten_queries = [rewritten_queries]
        rewritten_queries = list(rewritten_queries or [])
        # 用 dict.fromkeys 去重（保持顺序），原始 query 优先
        return list(dict.fromkeys([query, *rewritten_queries]))

    @classmethod
    async def query_rewrite(cls, query):
        """调用查询改写器，将用户问题改写为多个候选查询。"""
        query_list = await query_rewriter.rewrite(query)
        return query_list

    @classmethod
    async def _retrieve_field_results(cls, query_list, knowledges_id, search_field, index_names=None):
        """按指定字段（content/summary）从向量库与 ES 检索文档。"""
        if isinstance(query_list, str):
            query_list = [query_list]
        if isinstance(knowledges_id, str):
            knowledges_id = [knowledges_id]
        if isinstance(index_names, str):
            index_names = [index_names]
        if app_settings.rag.enable_elasticsearch:
            # 启用 ES：向量库 + ES 双路召回，各自按分数降序后拼接
            es_documents, milvus_documents = await MixRetrival.mix_retrival_documents(
                query_list, knowledges_id, search_field, index_names
            )
            es_documents.sort(key=lambda doc: doc.score, reverse=True)
            milvus_documents.sort(key=lambda doc: doc.score, reverse=True)
            return es_documents + milvus_documents
        # 未启用 ES：仅走向量库召回
        return await MixRetrival.retrival_milvus_documents(
            query_list, knowledges_id, search_field
        )

    @classmethod
    async def index_milvus_documents(cls, collection_name, chunks):
        """将文档分块写入向量库（Milvus/Chroma）。"""
        await milvus_client.insert(collection_name, chunks)

    @classmethod
    async def index_es_documents(cls, index_name, chunks):
        """将文档分块写入 Elasticsearch 索引。"""
        await es_client.index_documents(index_name, chunks)

    @classmethod
    async def mix_retrival_documents(cls, query_list, knowledges_id, search_field="summary", index_names=None):
        """混合召回：支持 content+summary 双字段召回，并按分数去重合并。"""
        if search_field == "content+summary":
            # 双字段召回：内容与摘要分别检索后合并
            content_documents = await cls._retrieve_field_results(
                query_list, knowledges_id, "content", index_names
            )
            summary_documents = await cls._retrieve_field_results(
                query_list, knowledges_id, "summary", index_names
            )
            all_documents = content_documents + summary_documents
        else:
            # 单字段召回
            all_documents = await cls._retrieve_field_results(
                query_list, knowledges_id, search_field, index_names
            )

        # 按分数排序、按 chunk_id 去重，取前 10 条
        documents = merge_documents_by_score(all_documents, top_k=10)
        return documents

    @classmethod
    def _filter_reranked_documents(cls, reranked_docs, min_score, rerank_threshold, top_k):
        """过滤重排结果：剔除低于分数阈值（min_score / rerank_threshold）的文档并截取 top_k。"""
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
        """基于摘要字段的 RAG 查询：改写 → 摘要召回 → 重排 → 过滤拼接。"""
        # 从配置读取默认阈值参数
        if min_score is None:
            min_score = app_settings.rag.retrival.get('min_score')
        if top_k is None:
            top_k = app_settings.rag.retrival.get('top_k')
        if rerank_threshold is None:
            rerank_threshold = app_settings.rag.retrival.get('rerank_threshold')
        if isinstance(knowledges_id, str):
            knowledges_id = [knowledges_id]

        # 查询重写：生成多个候选查询提升召回
        if needs_query_rewrite:
            rewritten_queries = await cls.query_rewrite(query)
            rewritten_queries = cls._normalize_rewritten_queries(
                query, rewritten_queries
            )
        else:
            rewritten_queries = [query]

        # 文档检索（摘要字段）
        retrieved_documents = await cls.mix_retrival_documents(rewritten_queries, knowledges_id, "summary")

        # 准备重排序的文档内容
        documents_to_rerank = [doc.content for doc in retrieved_documents]

        # 文档重排序
        reranked_docs = await Reranker.rerank_documents(query, documents_to_rerank)

        if reranked_docs is None:
            # 重排不可用：回退到原始摘要检索分数
            logger.warning("Rerank unavailable, fallback to raw summary retrieval scores")
            filtered_results = cls._filter_reranked_documents(
                retrieved_documents, min_score, None, top_k
            )
            if not filtered_results:
                return "No relevant documents found."
            return "\n".join(result.content for result in filtered_results)

        # 确保top_k不为None
        actual_top_k = top_k if top_k is not None else len(reranked_docs)
        if actual_top_k and len(reranked_docs) < actual_top_k:
            # 摘要召回数量不足 top_k：改用 content 字段重新召回
            logger.info("Recall for summary field numbers < top k, start recall using content field")
            return await cls.retrieve_ranked_documents(
                query,
                knowledges_id,
                min_score=min_score,
                top_k=top_k,
                needs_query_rewrite=needs_query_rewrite,
                rerank_threshold=rerank_threshold,
            )

        # 过滤重排结果并拼接
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
        if isinstance(collection_names, str):
            collection_names = [collection_names]
        if isinstance(index_names, str):
            index_names = [index_names]

        # 查询重写：生成多个候选查询提升召回
        if needs_query_rewrite:
            rewritten_queries = await cls.query_rewrite(query)
            rewritten_queries = cls._normalize_rewritten_queries(
                query, rewritten_queries
            )
        else:
            rewritten_queries = [query]

        # 文档检索（content 字段）
        retrieved_documents = await cls.mix_retrival_documents(
            rewritten_queries, collection_names, "content", index_names
        )

        # 准备重排序的文档内容
        documents_to_rerank = [doc.content for doc in retrieved_documents]

        # 文档重排序
        reranked_docs = await Reranker.rerank_documents(query, documents_to_rerank)

        if reranked_docs is None:
            # 重排不可用：回退到原始 content 检索分数
            logger.warning("Rerank unavailable, fallback to raw content retrieval scores")
            filtered_results = cls._filter_reranked_documents(
                retrieved_documents, min_score, None, top_k
            )
        else:
            # 重排可用：按重排分数过滤
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
        """删除文档：按 file_id 从 ES（若启用）与向量库中同步删除。"""
        if app_settings.rag.enable_elasticsearch:
            await es_client.delete_documents(file_id, knowledge_id)
        await milvus_client.delete_by_file_id(file_id, knowledge_id)
