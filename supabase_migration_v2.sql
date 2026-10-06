-- Pabrai-Analyzer v2 migration
-- 1) active investment universe (KOSPI top 200 + S&P 500)
-- 2) auto framework scores/history
-- 3) foundation for Buffett-style / Munger-style analysis

begin;

create table if not exists public.investment_universe (
    company_id uuid not null
        references public.companies(id) on delete cascade,
    universe text not null
        check (universe in ('KOSPI_TOP200','SP500','CUSTOM')),
    rank integer,
    is_active boolean not null default true,
    as_of date not null default current_date,
    updated_at timestamptz not null default now(),
    primary key (company_id, universe)
);

create index if not exists investment_universe_active_idx
    on public.investment_universe (universe, is_active, rank);

create table if not exists public.frameworks (
    id text primary key,
    name text not null,
    framework_type text not null,
    description text,
    is_official boolean not null default false,
    is_active boolean not null default true,
    created_at timestamptz not null default now()
);

create table if not exists public.framework_dimensions (
    framework_id text not null
        references public.frameworks(id) on delete cascade,
    dimension_key text not null,
    dimension_name text not null,
    weight_pct numeric(8,4) not null
        check (weight_pct >= 0 and weight_pct <= 100),
    description text,
    display_order integer not null default 0,
    primary key (framework_id, dimension_key)
);

create table if not exists public.framework_scores (
    company_id uuid not null
        references public.companies(id) on delete cascade,
    framework_id text not null
        references public.frameworks(id) on delete cascade,
    total_score numeric(6,2)
        check (total_score is null or (total_score >= 0 and total_score <= 100)),
    coverage_pct numeric(6,2) not null default 0
        check (coverage_pct >= 0 and coverage_pct <= 100),
    status text not null default 'PENDING',
    details jsonb not null default '{}'::jsonb,
    as_of date not null default current_date,
    updated_at timestamptz not null default now(),
    primary key (company_id, framework_id)
);

create index if not exists framework_scores_rank_idx
    on public.framework_scores (framework_id, total_score desc nulls last);

create table if not exists public.framework_score_history (
    id uuid primary key default gen_random_uuid(),
    company_id uuid not null
        references public.companies(id) on delete cascade,
    framework_id text not null
        references public.frameworks(id) on delete cascade,
    total_score numeric(6,2),
    coverage_pct numeric(6,2) not null default 0,
    status text not null,
    details jsonb not null default '{}'::jsonb,
    as_of date not null,
    created_at timestamptz not null default now()
);

create index if not exists framework_score_history_company_idx
    on public.framework_score_history (
        company_id,
        framework_id,
        as_of desc
    );

insert into public.frameworks
    (id, name, framework_type, description, is_official, is_active)
values
    (
        'PABRAI_AUTO',
        'Pabrai Score (자동 1차)',
        'AUTO_QUANT',
        'Pabrai-style 213 체크리스트의 우선순위를 기반으로 공개 재무/시장 데이터에서 자동 계산한 정량 프록시. 정성 판단을 완전히 대체하지 않음.',
        false,
        true
    ),
    (
        'BUFFETT_STYLE',
        'Buffett-style Framework',
        'HYBRID',
        '경제적 해자, 사업의 질, 자본배분, 재무건전성, 내재가치 중심의 확장용 프레임워크. 공식 Buffett 점수표가 아님.',
        false,
        true
    ),
    (
        'MUNGER_STYLE',
        'Munger-style Framework',
        'HYBRID',
        '사업의 질, 해자, 인센티브, 재무건전성, 단순성/예측가능성, 심리적 오류 및 리스크를 통합하기 위한 확장용 프레임워크. 공식 Munger 점수표가 아님.',
        false,
        true
    )
on conflict (id) do update
set name = excluded.name,
    framework_type = excluded.framework_type,
    description = excluded.description,
    is_official = excluded.is_official,
    is_active = excluded.is_active;

insert into public.framework_dimensions
    (framework_id, dimension_key, dimension_name, weight_pct, description, display_order)
values
    ('PABRAI_AUTO','leverage','Leverage',24,'부채, 유동성, 생존성',1),
    ('PABRAI_AUTO','moat','Moat',22,'경쟁우위의 정량 프록시',2),
    ('PABRAI_AUTO','management','Management & Ownership',20,'자본배분/효율/이해관계 프록시',3),
    ('PABRAI_AUTO','business','Business Economics',8,'사업 경제성',4),
    ('PABRAI_AUTO','accounting','Accounting',5,'이익의 질',5),
    ('PABRAI_AUTO','valuation','Valuation',7,'가격 대비 가치 프록시',6),
    ('PABRAI_AUTO','external','External Risks',3,'외부 리스크 프록시',7),
    ('PABRAI_AUTO','failure','Failure Points',5,'영구손실 가능성 프록시',8),
    ('PABRAI_AUTO','circle','Circle of Competence',4,'자동화 제외: 사용자/정성 판단 영역',9),
    ('PABRAI_AUTO','bias','Personal Biases',2,'자동화 제외: 투자자 자기점검 영역',10),

    ('BUFFETT_STYLE','moat','Durable Moat',25,'지속 가능한 경쟁우위',1),
    ('BUFFETT_STYLE','quality','Business Quality',20,'높은 자본효율과 현금창출력',2),
    ('BUFFETT_STYLE','capital_allocation','Management & Capital Allocation',20,'주주가치 중심의 자본배분',3),
    ('BUFFETT_STYLE','financial_strength','Financial Strength',15,'재무건전성과 생존성',4),
    ('BUFFETT_STYLE','intrinsic_value','Intrinsic Value & Margin of Safety',15,'내재가치 대비 가격',5),
    ('BUFFETT_STYLE','predictability','Predictability',5,'사업의 단순성과 예측가능성',6),

    ('MUNGER_STYLE','quality','Business Quality',20,'훌륭한 사업의 경제성',1),
    ('MUNGER_STYLE','moat','Moat',20,'지속 가능한 경쟁우위',2),
    ('MUNGER_STYLE','incentives','Incentives & Management',20,'인센티브 구조와 경영진',3),
    ('MUNGER_STYLE','financial_strength','Financial Strength',15,'재무적 생존성',4),
    ('MUNGER_STYLE','valuation','Valuation',10,'합리적 가격',5),
    ('MUNGER_STYLE','simplicity','Simplicity & Predictability',10,'이해 가능성과 예측가능성',6),
    ('MUNGER_STYLE','psychology','Psychology & Risk',5,'인지편향과 치명적 리스크',7)
on conflict (framework_id, dimension_key) do update
set dimension_name = excluded.dimension_name,
    weight_pct = excluded.weight_pct,
    description = excluded.description,
    display_order = excluded.display_order;

alter table public.investment_universe enable row level security;
alter table public.frameworks enable row level security;
alter table public.framework_dimensions enable row level security;
alter table public.framework_scores enable row level security;
alter table public.framework_score_history enable row level security;

revoke all on table public.investment_universe from anon, authenticated;
revoke all on table public.frameworks from anon, authenticated;
revoke all on table public.framework_dimensions from anon, authenticated;
revoke all on table public.framework_scores from anon, authenticated;
revoke all on table public.framework_score_history from anon, authenticated;

grant select, insert, update, delete on table public.investment_universe to service_role;
grant select, insert, update, delete on table public.frameworks to service_role;
grant select, insert, update, delete on table public.framework_dimensions to service_role;
grant select, insert, update, delete on table public.framework_scores to service_role;
grant select, insert, update, delete on table public.framework_score_history to service_role;

commit;

select
    (select count(*) from public.frameworks) as framework_count,
    (select count(*) from public.framework_dimensions) as dimension_count;
