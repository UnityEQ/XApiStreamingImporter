package org.gephi.plugins.xapi.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import java.util.Collections;
import java.util.List;

@JsonIgnoreProperties(ignoreUnknown = true)
public class SearchResponse {

    @JsonProperty("data")
    public List<Tweet> data;

    @JsonProperty("includes")
    public Includes includes;

    @JsonProperty("meta")
    public Meta meta;

    public List<Tweet> safeData() {
        return data == null ? Collections.emptyList() : data;
    }
}
