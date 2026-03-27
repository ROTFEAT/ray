"""
Ray Tune + Optuna 调参测试
在 Ray 集群上用 Optuna 搜索 Ackley 函数的全局最小值 (最优解在原点, 值为0)
压力测试: 1000 trials, 248 并发, 20维搜索空间
"""
import numpy as np
import ray
from ray import tune
from ray.tune.search.optuna import OptunaSearch

def ackley(config):
    """Ackley function - 经典多维优化测试函数"""
    x = np.array([config[f"x{i}"] for i in range(20)])
    a, b, c = 20, 0.2, 2 * np.pi
    n = len(x)
    sum1 = np.sum(x ** 2)
    sum2 = np.sum(np.cos(c * x))
    result = -a * np.exp(-b * np.sqrt(sum1 / n)) - np.exp(sum2 / n) + a + np.e
    # 模拟一些计算开销
    for _ in range(50):
        np.linalg.svd(np.random.randn(100, 100))
    return {"loss": result}

def main():
    ray.init()

    resources = ray.cluster_resources()
    total_cpu = int(resources.get("CPU", 0))
    print(f"Cluster: {total_cpu} CPUs, {resources.get('memory', 0) / 1e9:.0f} GB memory")

    search_space = {f"x{i}": tune.uniform(-5, 5) for i in range(20)}

    optuna_search = OptunaSearch(metric="loss", mode="min")

    tuner = tune.Tuner(
        ackley,
        param_space=search_space,
        tune_config=tune.TuneConfig(
            search_alg=optuna_search,
            num_samples=1000,
            max_concurrent_trials=total_cpu,
        ),
        run_config=tune.RunConfig(
            name="optuna_stress_test",
            verbose=1,
        ),
    )

    results = tuner.fit()

    best = results.get_best_result(metric="loss", mode="min")
    print(f"\nBest loss: {best.metrics['loss']:.6f}")
    print(f"Best params: { {k: f'{v:.4f}' for k, v in best.config.items()} }")
    print(f"Total trials: {len(results)}")

    ray.shutdown()

if __name__ == "__main__":
    main()
