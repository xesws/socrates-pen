"""Primary reviewer's prewritten answer probes; never reads model rubric outputs.

Run after source review, before freezing/API calls. Refuses to change a frozen set.
Full/partial answers select known source facts; paraphrases/misconceptions below
are independently authored. This is synthetic gold, not human double annotation.
"""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / 'evals/rl_textbook_v1'

# (complete equivalent paraphrase, wrong answer, contradiction of fact 1)
VARIANTS = {
'RL01Q08': (
    'The agent is the robot control software deciding where to drive and how to pick and place. The world contains shelves, stock, order queues, obstacles, workers and robot dynamics. Its decision input includes pose, target item, gripper status, a local map, order progress and safety observations. Available controls include moving, turning, picking, placing, waiting and choosing a route. Reward successful correct orders, safe collision-free operation and efficient travel, not speed alone. End a run on completion, timeout, serious collision or exhausted battery. Occluded people and missing unfinished-order information can make the observation insufficiently Markov. Measure success, time, collisions, travel distance and performance on unfamiliar layouts.',
    '智能体是货架本身；控制程序属于被动环境。状态不需要任何订单或位置。动作就是成功率这个统计结果。只奖励撞人次数，永远不终止，不检查历史依赖，也不做评估。',
    '智能体绝不是控制机器人的决策程序。'),
'RL01Q10': (
    'An episodic interaction has separate runs with genuine endpoints. A continuing interaction has no natural final step. For the former, optimize the expected sum of rewards within each run, optionally discounted. For an ongoing task use a discounted objective or long-run average reward instead of an undefined infinite sum. Chess, level completion and an individual delivery are episodic examples. Temperature regulation, ongoing server scheduling and continuous recommendations fit continuing interaction. Report episode return, success and length for episodic tasks; steady-state average outcomes for continuing ones. Choose the representation to match the actual temporal structure so the objective does not distort preferences.',
    'episodic 永远没有终点，continuing 必须每一步结束。两者目标都是让奖励消失；棋局没有结束，持续控温只能有一局；不需指标，任意改时间结构不会影响策略。',
    'episodic 任务绝不包含有终点的 episode。'),
'RL02Q08': (
    'Split the return into the next reward plus gamma times the remaining return. Condition on the present state s and keep policy pi fixed. Average over actions drawn from pi now and later. Also integrate the random transitions and random rewards of the environment. R at t+1 is the immediate feedback caused by the current action. Gamma scales the continuation value. The resulting identity says that the policy value is a fixed point of its own Bellman backup. It supplies policy evaluation and the targets used in DP and temporal-difference learning.',
    '回报递推应为 G_t=R_{t+1}-G_{t+1}，不需要折扣或条件。对下一随机状态直接取最大就代替所有期望；奖励是在动作前产生的固定标签。方程不涉及任何价值固定点，也不能用于策略评价。',
    'G_t=R_{t+1}+γG_{t+1} 这个递推等式是错误的。'),
'RL02Q10': (
    'Inventory the fields observable at the decision time. Look for omitted drivers such as stock availability, latent user intent or device delays. Conditional on those fields and the action, test whether older history still predicts the next observation. Test the same conditional dependence for rewards. If click sequences or accumulated fatigue add predictive information, the current record is not a sufficient Markov state. Add the relevant history summary, time features or a belief representation. Compare history-aware and memoryless predictors and resulting policy performance. Aliasing can bias Bellman-based learning and harm deployment stability, rather than guaranteeing failure in every case.',
    '任何当前日志天然满足马尔可夫性，不用查看字段、隐藏变量或历史。下一观测和奖励无论如何都不能依赖历史，因此不要补充状态或验证；信息缺失永远不影响学习。',
    '完全没有必要列出日志当前可见的观测字段。'),
'RL03Q08': (
    'Represent each grid location as a state with the appropriate terminal cells. Offer four directions and specify that blocked movement stays put. Supply transition probabilities for each action, including any slipping. Define rewards explicitly, for example a step cost of minus one and zero on reaching the goal. Evaluate the current policy by averaging action probabilities and transition outcomes in the value backup. Improve each nonterminal state by maximizing immediate expected reward plus discounted continuation among legal actions. Stop when the policy stabilizes or value changes are sufficiently small. Return the policy, value table, discount and convergence tolerance.',
    '状态只用一个随机数且与网格无关，动作是输出奖励值；不需要转移模型也能做精确DP。评价时把概率全部忽略，改进选非法动作，永远不设停止条件且不输出策略或价值。',
    '网格位置不能作为这个 Gridworld 的状态。'),
'RL03Q10': (
    'The desired value solves a Bellman fixed-point equation. For discount below one the Bellman map shrinks sup-norm distances by at most gamma. A discount closer to one weakens this contraction and usually slows convergence. The classical tabular statement uses finite state and action sets. Asynchronous schemes still need sufficient coverage of the relevant updates. An inaccurate transition or reward model yields a solution to that inaccurate model. Approximation of the value or truncation of policy evaluation adds further error. Do not apply this guarantee unchanged to undiscounted continuing tasks or a changing environment.',
    'Bellman 方程不存在固定点。折扣越大压缩必然越强；只更新一个无关状态即可保证所有连续非平稳任务精确收敛。模型错误、函数近似和未折扣无限任务都无需条件。',
    'Bellman 方程不会把目标价值定义为算子的固定点。'),
'RL04Q08': (
    'Initialize an action-value table, return statistics and an epsilon-greedy policy. Roll out full episodes with the current such policy. Accumulate discounted returns backwards from the end. Decide on first-visit or every-visit treatment and use it consistently. Update observed state-action values with the sample mean or its incremental equivalent. Positive epsilon leaves a nonzero chance for nongreedy actions. Recompute the epsilon-greedy policy from the new Q estimates. Specify a stopping budget or performance-stability rule and the exploration schedule.',
    '不初始化Q也不收集完整轨迹；每一步用下一状态估值当作MC完整回报。重复访问随便处理，只选贪心动作且从不更新策略或设置停止规则。',
    '算法不应该初始化 Q、回报统计或 epsilon-greedy 策略。'),
'RL04Q10': (
    'Monte Carlo is a natural choice for episodic problems with genuine endings. It learns from sampled complete trajectories without a known dynamics model. It is convenient when episodes are short enough that waiting for their end is acceptable. With a fixed policy and independent complete rollouts from the specified state, sampled returns have the correct value in expectation. The target does not plug in a possibly poor estimate of the next state value. Variance can be large so average enough episodes. A sparse terminal outcome supplies a return for visits along the completed trajectory. Implementation is simple, while TD is generally more convenient for immediate online updates and continuing tasks.',
    'MC 最适合永远不结束的任务，并且必须知道精确模型。它必须以当前下一状态估值自举，单条轨迹就绝无方差；终局奖励不能用于之前访问，在线更新永远比TD更及时。',
    '有自然终点的 episodic 任务不适合 MC。'),
'RL05Q08': (
    'SARSA uses r plus gamma Q at the successor state and the actually sampled next action. Q-learning instead uses r plus gamma times the largest successor action value. The SARSA next action is drawn from the behavior policy. Q-learning evaluates a greedy target even when behavior explores. SARSA therefore reflects the consequences of exploratory moves and can prefer a more cautious route. Tabular guarantees need adequate visitation, appropriate learning rates and the stated finite discounted setting. With approximation either method can be unstable, with off-policy bootstrapping an additional concern for Q-learning. SARSA is useful when exploration risk matters; Q-learning is common for learning greedy control.',
    'SARSA 的目标必须用 max，Q-learning 必须用实际下一动作；SARSA 是完全不考虑探索的离策略方法，任何步长和数据覆盖都保证神经网络收敛，二者适用范围没有任何差别。',
    'SARSA 的目标不是 R+γQ(S\',A\')。'),
'RL05Q10': (
    'TD can update after a transition, whereas Monte Carlo normally waits for the full episode. Very long runs or lack of a natural endpoint favor TD in practice. Monte Carlo uses realized complete returns without the bootstrap target bias from an estimated successor value. A TD target usually has less sampling variance but relies on current estimates. Favor TD when adaptation must occur while the system runs. Continuing tasks can use an appropriate discounted or average-reward TD formulation. If a delayed terminal reward is eventually observable, Monte Carlo directly uses it. TD can be implemented without keeping an entire episode, which is useful with limited memory and compute.',
    'TD 必须等终局，MC 每步都能知道完整未来。任务越长MC越适合即时更新；MC只能自举而TD永不依赖估值。continuing任务完全不能用TD，且TD必须存下全部无限轨迹。',
    'TD 无法逐步更新，而 MC 的完整回报无需等待 episode 结束。'),
'RL06Q08': (
    'Scale or normalize input features sensibly. Randomize minibatches from an experience buffer to reduce temporal correlation. Compute targets with a delayed network updated periodically or softly. A Huber objective or bounded influence from large TD errors reduces outlier effects. Define an epsilon-greedy exploration annealing schedule. Remove bootstrapping at genuine terminal transitions; a mere time-limit truncation of a continuing task need not remove it. Track return, loss, TD residuals, Q magnitudes and the action distribution. Evaluate across several seeds with a fixed protocol and saved configurations.',
    '输入数值越失衡越好，只用强相关连续样本且不要目标网络。损失鼓励误差爆炸，停止一切探索；真正终止后也必须保留无限未来价值，不看任何监控或随机种子。',
    '输入不应做任何合理缩放或归一化，优化尺度失衡也无需处理。'),
'RL06Q10': (
    'When action-value estimates are noisy, maximizing them tends to select an upward error. The successor-action maximum in the Q-learning target is where this selection enters the update. Even equally valuable actions can show this effect because the largest noise realization wins. Inflated Q estimates can promote bad actions and unstable training. Double DQN selects the successor action with the online network and evaluates it using the target network. A delayed target alone slows target movement but does not remove selection bias. Compare predicted Q with empirical returns and inspect suspicious action concentration. Double DQN mitigates this issue; exploration, stable optimization and data coverage still matter.',
    'max 会系统性选择被低估的值，噪声完全不能导致偏差。Double DQN 用同一个网络同时选动作和评价即可彻底解决一切问题；不需要实际回报验证、探索或数据覆盖。',
    '含噪声时，max 不会更容易选到被高估的动作。'),
'RL07Q08': (
    'For this finite-horizon undiscounted objective, start with a parameterized stochastic policy. Generate complete episodes using the current policy. At each visited step compute the remaining reward sum. Multiply that return by the gradient of the sampled action log probability and accumulate the estimate. Subtracting a state-conditioned, action-independent baseline can reduce variance. Ascend the gradient to favor actions with positive advantage. Noisy trajectory returns, especially on long episodes, cause large variance. Assess learning on separate evaluation episodes using mean return and uncertainty intervals.',
    'REINFORCE 不需要随机参数策略，也无需采样轨迹和回报；直接对不可导环境动作求导。baseline 任意依赖当前动作仍永远无偏，按梯度下降减小目标回报，只看一次训练loss就能评价。',
    'REINFORCE 不应该初始化参数化随机策略 π_θ(a|s)。'),
'RL07Q10': (
    'Use a baseline conditioned on the state rather than the chosen action to remove common return fluctuations. Center the learning signal as an advantage so it reflects relative action quality. Larger batches or parallel trajectories reduce sampling noise. Multi-step estimators or GAE allow a bias-variance tradeoff. Standardizing advantages often improves numerical scaling. Entropy regularization helps maintain exploration instead of becoming deterministic because of noisy early evidence. Clip gradients or constrain policy movement to avoid one excessive update. Report return, entropy, value prediction error and KL or another measure of policy change.',
    '用只依赖动作且任意变化的baseline才能确保无偏；advantage应当是随机标签。batch越小噪声必然越小，GAE没有偏差方差权衡；归一化、熵和更新约束都毫无用途，完全不报告指标。',
    '仅依赖状态的 baseline 不应当用于降低回报的共同波动。'),
'RL08Q08': (
    'Separate training and evaluation through clearly defined environments, levels and random seeds. Freeze the evaluation policy mode, including any exploration epsilon. Use multiple seeds and report their mean and variability, not the best run. Include return, success, episode length, constraint violations and resource use. Quantify uncertainty using standard errors or confidence/bootstrap intervals. Compare against suitable random, heuristic, established and no-learning baselines. Ablate the proposed components to test the claimed cause of improvement. Document failures, unusual trajectories and behavior outside the training distribution.',
    '评估直接使用调参时挑出的最好训练轨迹，不固定策略、不用多个种子；只报最高奖励，隐藏失败和安全事件，不需区间、基线或消融。',
    '训练与评估环境、种子或关卡完全不需要清楚隔离。'),
'RL08Q10': (
    'Epsilon-greedy explores via a fixed or scheduled random-action probability. UCB adds a count- or uncertainty-based bonus. Intrinsic reward supplies extra signal for novelty, prediction error or information gain. Epsilon-greedy is simple but does not prioritize uncertain actions. UCB directs exploration but needs credible counts or uncertainty estimates. Intrinsic motivation helps discover sparse external rewards but can keep chasing novelty rather than the task. All three require action constraints and risk filtering in safety-sensitive settings. Separately report gains during exploration, final policy quality and safety violations.',
    'epsilon-greedy 从不随机；UCB 完全不使用计数和不确定性；内在奖励就是删除新奇信号。三者保证绝对安全，不需调节或评估，最终策略表现也不用记录。',
    'epsilon-greedy 不会以固定或调度的概率随机选动作。'),
}


def main():
    if (FIX/'manifest.json').exists():raise SystemExit('Dataset frozen; refusing to overwrite probes')
    expected=json.loads((FIX/'expected.json').read_text())
    questions=expected['questions'] if isinstance(expected,dict) else expected
    rows=[]
    for q in questions:
        ident=q['title'].split()[0]
        if ident not in VARIANTS:continue
        facts=q.get('facts') or [re.sub(r'^\d+\.\s*','',line) for line in q['reference_answer'].splitlines() if re.match(r'^\d+\. ',line)]
        if len(facts)!=8:raise ValueError('Expected eight reviewed source facts: '+ident)
        paraphrase,wrong,contradiction=VARIANTS[ident]
        weights=[.1,.1,.15,.15,.1,.1,.15,.15]
        full='\n'.join(facts)
        for kind,text,values in [
            ('full',full,weights),('paraphrase',paraphrase,weights),
            ('basic','\n'.join(facts[:2]),weights[:2]+[0]*6),
            ('advanced','\n'.join(facts[:4]),weights[:4]+[0]*4),
            ('wrong',wrong,[0]*8),
            ('contradiction',full+'\n我同时坚持以下相反说法：'+contradiction,[0]+weights[1:]),
        ]:
            rows.append({'case_id':ident,'kind':kind,'text':text,'score':round(sum(values),6),
                         'criterion_scores':{f'fact{i+1}':v for i,v in enumerate(values)}})
    if len(rows)!=96:raise ValueError('Expected 96 probes')
    (FIX/'probe-answers.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2)+'\n')
    print('Wrote 96 reviewer-authored probes before any rubric generation')


if __name__=='__main__':main()
