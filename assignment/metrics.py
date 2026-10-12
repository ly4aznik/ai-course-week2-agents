"""Repeat-level and task-level metrics; Wilson is descriptive for correlated repeats."""
import math
from statistics import mean


def wilson(successes, total, z=1.959963984540054):
    if total==0:
        raise ValueError('Empty sample')
    p=successes/total
    denom=1+z*z/total
    center=(p+z*z/(2*total))/denom
    half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return max(0.,center-half),min(1.,center+half)


def aggregate(records, n):
    result={}
    for variant in ('short','full'):
        group=[r for r in records if r['variant']==variant and not r.get('diagnostic')]
        by_task={task:[r for r in group if r['task']==task] for task in sorted({r['task'] for r in group})}
        if not group or any(len(rs)!=n for rs in by_task.values()):
            raise ValueError('Incomplete experiment')
        selection=sum(r['evaluation']['tool_args_correct'] for r in group)
        result[variant]={'attempts':len(group),'tasks':len(by_task),'pass_at_1':mean(r['evaluation']['passed'] for r in group),
            'first_repeat_success':mean(r['evaluation']['passed'] for r in group if r['repeat']==1),
            'pass_power_n':mean(all(r['evaluation']['passed'] for r in rs) for rs in by_task.values()),
            'tool_args_successes':selection,'tool_args_rate':selection/len(group),'wilson95':wilson(selection,len(group)),
            'mean_input_tokens':mean(r['input_tokens'] for r in group),'mean_output_tokens':mean(r['output_tokens'] for r in group),
            'mean_total_tokens':mean(r['input_tokens']+r['output_tokens'] for r in group),
            'mean_cost_usd':mean(r['cost_usd'] for r in group),'mean_steps':mean(r['steps'] for r in group),
            'total_cost_usd':sum(r['cost_usd'] for r in group),
            'cost_sources':sorted({s for r in group for s in r['cost_sources']})}
    a,b=result['short'],result['full']
    gained=b['tool_args_successes']-a['tool_args_successes']
    cost_delta=b['total_cost_usd']-a['total_cost_usd']
    result['comparison']={'additional_correct_attempts':gained,'additional_cost_usd':cost_delta,
        'additional_mean_input_tokens':b['mean_input_tokens']-a['mean_input_tokens'],
        'cost_per_additional_correct_attempt_usd':cost_delta/gained if gained>0 else None,
        'cost_per_correct_short':a['total_cost_usd']/a['tool_args_successes'] if a['tool_args_successes'] else None,
        'cost_per_correct_full':b['total_cost_usd']/b['tool_args_successes'] if b['tool_args_successes'] else None}
    return result
