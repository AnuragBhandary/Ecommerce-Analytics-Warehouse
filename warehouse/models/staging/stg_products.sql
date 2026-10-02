select
    p.product_id,
    coalesce(t.category_name_english, p.category_name, 'unknown') as category,
    p.category_name as category_pt,
    p.name_length,
    p.description_length,
    p.photos_qty,
    p.weight_g,
    p.length_cm,
    p.height_cm,
    p.width_cm,
    p._loaded_at
from {{ source('raw', 'products') }} p
left join {{ source('raw', 'category_translation') }} t using (category_name)
